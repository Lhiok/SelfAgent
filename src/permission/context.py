"""权限上下文：模式、规则桶、会话 once、Plan 只读配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from permission.rules import parse_rule, parse_rule_list, serialize_rule
from permission.types import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
)

PreToolHook = Callable[
    [str, str | None, dict[str, Any]],
    PermissionDecision | None,
]


@dataclass
class PermissionContext:
    enabled: bool = True
    mode: PermissionMode = PermissionMode.DEFAULT
    default_effect: str = "deny"  # allow | deny
    allow_rules: list[PermissionRule] = field(default_factory=list)
    deny_rules: list[PermissionRule] = field(default_factory=list)
    ask_rules: list[PermissionRule] = field(default_factory=list)
    session_allow: list[PermissionRule] = field(default_factory=list)
    session_deny: list[PermissionRule] = field(default_factory=list)
    workdir: Path | None = None
    plan_active: bool = False
    allow_readonly_in_plan: bool = True
    readonly_actions: dict[str, set[str]] = field(default_factory=dict)
    plan_allow_skills: set[str] = field(default_factory=set)
    pre_tool_hooks: list[PreToolHook] = field(default_factory=list)

    def set_mode(self, mode: str | PermissionMode) -> None:
        self.mode = PermissionMode.parse(mode)

    def set_plan_active(self, active: bool) -> None:
        self.plan_active = bool(active)

    def set_workdir(self, workdir: str | Path | None) -> None:
        if workdir is None or (isinstance(workdir, str) and not workdir.strip()):
            self.workdir = None
        else:
            self.workdir = Path(workdir).expanduser()

    def bind_plan(
        self,
        *,
        allow_readonly_in_plan: bool = True,
        readonly_actions: dict[str, set[str] | list[str]] | None = None,
        plan_allow_skills: Iterable[str] | None = None,
    ) -> None:
        self.allow_readonly_in_plan = bool(allow_readonly_in_plan)
        self.readonly_actions = {
            str(k): {str(a).strip().lower() for a in (v or []) if str(a).strip()}
            for k, v in (readonly_actions or {}).items()
        }
        self.plan_allow_skills = {
            str(s).strip() for s in (plan_allow_skills or []) if str(s).strip()
        }

    def grant_session(self, rule_raw: str) -> PermissionRule:
        """本会话允许（once）。"""
        rule = parse_rule(rule_raw, PermissionBehavior.ALLOW, source="session")
        self.session_allow.append(rule)
        return rule

    def deny_session(self, rule_raw: str) -> PermissionRule:
        rule = parse_rule(rule_raw, PermissionBehavior.DENY, source="session")
        self.session_deny.append(rule)
        return rule

    def add_rules(
        self,
        *,
        allow: Iterable[str] | None = None,
        deny: Iterable[str] | None = None,
        ask: Iterable[str] | None = None,
        source: str = "config",
    ) -> None:
        self.allow_rules.extend(parse_rule_list(allow, PermissionBehavior.ALLOW, source=source))
        self.deny_rules.extend(parse_rule_list(deny, PermissionBehavior.DENY, source=source))
        self.ask_rules.extend(parse_rule_list(ask, PermissionBehavior.ASK, source=source))

    def replace_allow_from_legacy(self, rules: list[PermissionRule]) -> None:
        """用 legacy 角色编译结果替换 legacy 来源的 allow，保留显式 config allow。"""
        kept = [r for r in self.allow_rules if r.source != "legacy"]
        self.allow_rules = kept + list(rules)

    def all_allow(self) -> list[PermissionRule]:
        return list(self.session_allow) + list(self.allow_rules)

    def all_deny(self) -> list[PermissionRule]:
        return list(self.session_deny) + list(self.deny_rules)

    def suggest_allow(self, skill: str, action: str | None) -> str:
        return serialize_rule(skill, action)
