"""权限守卫：按角色限制可调用的 Skill 与 action。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

import config as cfg
from log import get_logger

logger = get_logger("permission")


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""


@dataclass
class SkillRule:
    """单个 Skill 的权限规则。actions 为 None 表示该 Skill 下全部 action 放行。"""

    allow: bool = True
    actions: set[str] | None = None  # None = 全部；空集 = 无可执行 action


@dataclass
class RolePolicy:
    name: str
    # skill_name -> rule；支持 "*" 通配
    skills: dict[str, SkillRule] = field(default_factory=dict)


class PermissionGuard:
    """
    对 ReAct 调用 Skill 做鉴权。

    判定顺序：
    1. enabled=False → 全部放行
    2. 命中角色下的 skill 规则（精确名优先于 *）
    3. skill.allow=False → 拒绝
    4. 若配置了 actions 白名单，则检查 arguments 中的 action 字段
    5. 未命中任何规则 → 按 default_effect（allow/deny）
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        role: str = "default",
        default_effect: str = "deny",
        roles: dict[str, RolePolicy] | None = None,
    ) -> None:
        self.enabled = enabled
        self.role = role
        effect = (default_effect or "deny").strip().lower()
        if effect not in {"allow", "deny"}:
            raise ValueError(f"default_effect 只能是 allow/deny，收到: {default_effect}")
        self.default_effect = effect
        self.roles = roles or {}

    @classmethod
    def from_config(cls, role: str | None = None) -> "PermissionGuard":
        # 未配置 permission 节时不限权，保持向后兼容
        if "permission" not in cfg.get_config():
            return cls.allow_all()

        section = cfg.get_section("permission", {}) or {}
        enabled = bool(section.get("enabled", True))
        default_effect = str(section.get("default_effect", "deny"))
        active_role = role or str(section.get("role", "default"))

        roles: dict[str, RolePolicy] = {}
        raw_roles = section.get("roles") or {}
        if isinstance(raw_roles, dict):
            for name, body in raw_roles.items():
                roles[str(name)] = _parse_role(str(name), body or {})

        # 兼容简写：未写 roles 时，用 allow_skills / skill_rules 生成 default 角色
        if not roles:
            roles["default"] = _parse_legacy_role(section)

        if active_role not in roles:
            # 配置了角色名但未定义时，建空策略，走 default_effect
            roles[active_role] = RolePolicy(name=active_role)

        return cls(
            enabled=enabled,
            role=active_role,
            default_effect=default_effect,
            roles=roles,
        )

    @classmethod
    def allow_all(cls) -> "PermissionGuard":
        return cls(enabled=False, role="*", default_effect="allow")

    def use_role(self, role: str) -> "PermissionGuard":
        """切换角色（原地），便于同一守卫多场景复用。"""
        if role not in self.roles:
            self.roles[role] = RolePolicy(name=role)
        self.role = role
        return self

    def allowed_skills(self, skill_names: Iterable[str]) -> list[str]:
        """过滤出当前角色可见/可调用的 Skill 名。"""
        names = list(skill_names)
        if not self.enabled:
            return names
        return [n for n in names if self.check(n).allowed]

    def filter_schema_text(self, registry_schemas: dict[str, str]) -> str:
        """registry_schemas: skill_name -> schema_text"""
        allowed = self.allowed_skills(registry_schemas.keys())
        if not allowed:
            return "(当前角色无可用 Skill)"
        return "\n".join(registry_schemas[n] for n in sorted(allowed))

    def check(
        self,
        skill: str,
        *,
        action: str | None = None,
        arguments: dict[str, Any] | str | None = None,
    ) -> PermissionDecision:
        if not self.enabled:
            return PermissionDecision(True, "权限控制未启用")

        skill = (skill or "").strip()
        if not skill:
            return PermissionDecision(False, "Skill 名为空")

        args = _normalize_args(arguments)
        op = (action or args.get("action") or "").strip().lower() or None

        policy = self.roles.get(self.role) or RolePolicy(name=self.role)
        rule = policy.skills.get(skill) or policy.skills.get("*")

        if rule is None:
            if self.default_effect == "allow":
                return PermissionDecision(True, f"角色 {self.role} 未配置规则，默认放行")
            return PermissionDecision(
                False, f"角色 {self.role} 未授权 Skill: {skill}"
            )

        if not rule.allow:
            return PermissionDecision(False, f"角色 {self.role} 禁止 Skill: {skill}")

        if rule.actions is not None:
            if "*" in rule.actions:
                return PermissionDecision(True, "action 通配放行")
            if op is None:
                # 仅校验到 skill 层（列出 schema 时）
                return PermissionDecision(True, f"已授权 Skill: {skill}")
            if op not in rule.actions:
                return PermissionDecision(
                    False,
                    f"角色 {self.role} 禁止 {skill}.{op}，允许: {sorted(rule.actions)}",
                )

        return PermissionDecision(True, f"已授权 {skill}" + (f".{op}" if op else ""))

    def assert_allowed(
        self,
        skill: str,
        *,
        action: str | None = None,
        arguments: dict[str, Any] | str | None = None,
    ) -> PermissionDecision:
        decision = self.check(skill, action=action, arguments=arguments)
        if not decision.allowed:
            logger.warning(f"权限拒绝: {decision.reason}")
        return decision


def _normalize_args(arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    text = arguments.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_role(name: str, body: dict[str, Any]) -> RolePolicy:
    skills_raw = body.get("skills") or {}
    skills: dict[str, SkillRule] = {}
    if isinstance(skills_raw, dict):
        for skill_name, rule_body in skills_raw.items():
            skills[str(skill_name)] = _parse_skill_rule(rule_body)
    elif isinstance(skills_raw, list):
        # 简写: skills: [local_file, other]
        for skill_name in skills_raw:
            skills[str(skill_name)] = SkillRule(allow=True, actions=None)
    return RolePolicy(name=name, skills=skills)


def _parse_skill_rule(rule_body: Any) -> SkillRule:
    if rule_body is True or rule_body == "*":
        return SkillRule(allow=True, actions=None)
    if rule_body is False:
        return SkillRule(allow=False, actions=set())
    if isinstance(rule_body, list):
        actions = {str(a).strip().lower() for a in rule_body if str(a).strip()}
        return SkillRule(allow=True, actions=actions or set())
    if isinstance(rule_body, dict):
        allow = bool(rule_body.get("allow", True))
        raw_actions = rule_body.get("actions")
        if raw_actions is None:
            return SkillRule(allow=allow, actions=None)
        if raw_actions == "*" or raw_actions is True:
            return SkillRule(allow=allow, actions=None)
        if isinstance(raw_actions, list):
            actions = {str(a).strip().lower() for a in raw_actions if str(a).strip()}
            return SkillRule(allow=allow, actions=actions)
    return SkillRule(allow=True, actions=None)


def _parse_legacy_role(section: dict[str, Any]) -> RolePolicy:
    """兼容 allow_skills + skill_rules 简写。"""
    skills: dict[str, SkillRule] = {}
    allow_skills = section.get("allow_skills")
    skill_rules = section.get("skill_rules") or {}

    if isinstance(allow_skills, list):
        for name in allow_skills:
            skills[str(name)] = SkillRule(allow=True, actions=None)

    if isinstance(skill_rules, dict):
        for name, body in skill_rules.items():
            skills[str(name)] = _parse_skill_rule(body)

    if not skills and section.get("default_effect", "deny") == "allow":
        skills["*"] = SkillRule(allow=True, actions=None)

    return RolePolicy(name="default", skills=skills)
