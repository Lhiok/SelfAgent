"""权限守卫门面：规则引擎 + 可选 legacy roles 编译。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

import config as cfg
from log import get_logger
from permission.context import PermissionContext
from permission.legacy import compile_roles_section
from permission.pipeline import evaluate, normalize_args
from permission.types import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
    PermissionUpdate,
)

logger = get_logger("permission")

# ask 交互：返回 True 放行（并应 grant_session），False 拒绝
PermissionAskHandler = Callable[[dict[str, Any]], bool]


class PermissionGuard:
    """
    Agent → Skill 鉴权门面。

    判定走 permission.pipeline（deny → ask → plan/mode → allow → default）。
    命中 ask 且设置了 ask_handler 时，由 handler 交互决定（对齐 CC canUseTool）。
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        mode: str | PermissionMode = PermissionMode.DEFAULT,
        default_effect: str = "deny",
        allow: Iterable[str] | None = None,
        deny: Iterable[str] | None = None,
        ask: Iterable[str] | None = None,
        context: PermissionContext | None = None,
        # role 名用于 roles 再编译时的标记
        role: str = "default",
        ask_handler: PermissionAskHandler | None = None,
    ) -> None:
        effect = (default_effect or "deny").strip().lower()
        if effect not in {"allow", "deny"}:
            raise ValueError(f"default_effect 只能是 allow/deny，收到: {default_effect}")

        if context is not None:
            self.ctx = context
        else:
            self.ctx = PermissionContext(
                enabled=enabled,
                mode=PermissionMode.parse(mode),
                default_effect=effect,
            )
            self.ctx.add_rules(allow=allow, deny=deny, ask=ask, source="config")

        self.enabled = self.ctx.enabled
        self.default_effect = self.ctx.default_effect
        self.role = role  # legacy 字段；新模型以 rules 为准
        self.roles: dict[str, Any] = {}
        self.ask_handler: PermissionAskHandler | None = ask_handler

    def add_pre_tool_hook(self, hook: Callable[..., Any]) -> None:
        """注册 skill 自检钩子（对齐 pipeline 步骤 3）。"""
        self.ctx.pre_tool_hooks.append(hook)

    @classmethod
    def from_config(cls, role: str | None = None) -> "PermissionGuard":
        if "permission" not in cfg.get_config():
            return cls.allow_all()

        section = cfg.get_section("permission", {}) or {}
        enabled = bool(section.get("enabled", True))
        default_effect = str(section.get("default_effect", "deny"))
        mode = section.get("mode", PermissionMode.DEFAULT.value)
        active_role = role or str(section.get("role", "default"))

        guard = cls(
            enabled=enabled,
            mode=mode,
            default_effect=default_effect,
            role=active_role,
        )

        rules = section.get("rules") or {}
        if isinstance(rules, dict):
            guard.ctx.add_rules(
                allow=rules.get("allow"),
                deny=rules.get("deny"),
                ask=rules.get("ask"),
                source="config",
            )

        # legacy roles → rules
        if section.get("roles") or section.get("allow_skills") or section.get("skill_rules"):
            allow_l, deny_l = compile_roles_section(section, role=active_role)
            guard.ctx.replace_allow_from_legacy(allow_l)
            # legacy deny 追加（避免覆盖显式 deny）
            existing_deny = {r.raw for r in guard.ctx.deny_rules}
            for rule in deny_l:
                if rule.raw not in existing_deny:
                    guard.ctx.deny_rules.append(rule)

        return guard

    @classmethod
    def allow_all(cls) -> "PermissionGuard":
        return cls(enabled=False, mode=PermissionMode.BYPASS, default_effect="allow", role="*")

    @classmethod
    def from_rules(
        cls,
        *,
        allow: Iterable[str] | None = None,
        deny: Iterable[str] | None = None,
        ask: Iterable[str] | None = None,
        mode: str | PermissionMode = PermissionMode.DEFAULT,
        default_effect: str = "deny",
        enabled: bool = True,
    ) -> "PermissionGuard":
        return cls(
            enabled=enabled,
            mode=mode,
            default_effect=default_effect,
            allow=allow,
            deny=deny,
            ask=ask,
        )

    def use_role(self, role: str) -> "PermissionGuard":
        """若配置仍有 roles，按角色重新编译 allow。"""
        self.role = role
        section = cfg.get_section("permission", {}) or {}
        if section.get("roles"):
            allow_l, deny_l = compile_roles_section(section, role=role)
            self.ctx.replace_allow_from_legacy(allow_l)
            # 重建 deny：去掉旧 legacy deny 再加新的
            self.ctx.deny_rules = [r for r in self.ctx.deny_rules if r.source != "legacy"]
            self.ctx.deny_rules.extend(deny_l)
        return self

    def set_mode(self, mode: str | PermissionMode) -> "PermissionGuard":
        self.ctx.set_mode(mode)
        return self

    def set_plan_active(self, active: bool) -> "PermissionGuard":
        self.ctx.set_plan_active(active)
        return self

    def grant_session(self, rule_raw: str) -> PermissionRule:
        return self.ctx.grant_session(rule_raw)

    def bind_context(
        self,
        *,
        workdir: str | Path | None = None,
        allow_readonly_in_plan: bool | None = None,
        readonly_actions: dict[str, set[str] | list[str]] | None = None,
        plan_allow_skills: Iterable[str] | None = None,
        plan_active: bool | None = None,
    ) -> "PermissionGuard":
        if workdir is not None:
            self.ctx.set_workdir(workdir)
        if (
            allow_readonly_in_plan is not None
            or readonly_actions is not None
            or plan_allow_skills is not None
        ):
            self.ctx.bind_plan(
                allow_readonly_in_plan=(
                    self.ctx.allow_readonly_in_plan
                    if allow_readonly_in_plan is None
                    else allow_readonly_in_plan
                ),
                readonly_actions=(
                    self.ctx.readonly_actions
                    if readonly_actions is None
                    else readonly_actions
                ),
                plan_allow_skills=(
                    self.ctx.plan_allow_skills
                    if plan_allow_skills is None
                    else plan_allow_skills
                ),
            )
        if plan_active is not None:
            self.ctx.set_plan_active(plan_active)
        return self

    def allowed_skills(self, skill_names: Iterable[str]) -> list[str]:
        names = list(skill_names)
        if not self.ctx.enabled:
            return names
        return [n for n in names if self.check(n).allowed]

    def filter_schema_text(self, registry_schemas: dict[str, str]) -> str:
        allowed = self.allowed_skills(registry_schemas.keys())
        if not allowed:
            return "(当前权限策略下无可用 Skill)"
        return "\n".join(registry_schemas[n] for n in sorted(allowed))

    def check(
        self,
        skill: str,
        *,
        action: str | None = None,
        arguments: dict[str, Any] | str | None = None,
    ) -> PermissionDecision:
        return evaluate(self.ctx, skill, action=action, arguments=arguments)

    def assert_allowed(
        self,
        skill: str,
        *,
        action: str | None = None,
        arguments: dict[str, Any] | str | None = None,
    ) -> PermissionDecision:
        decision = self.check(skill, action=action, arguments=arguments)
        if not decision.allowed:
            extra = ""
            if decision.suggestions:
                tips = ", ".join(s.rule_raw for s in decision.suggestions)
                extra = f"；可会话放行: grant_session({tips!r})"
            logger.warning(f"权限拒绝: {decision.reason}{extra}")
        return decision


# 再导出，便于 `from permission.guard import PermissionDecision`
__all__ = [
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionGuard",
    "PermissionMode",
    "PermissionRule",
    "PermissionUpdate",
    "normalize_args",
]
