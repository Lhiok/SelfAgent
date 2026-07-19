"""将 roles / skill_rules 编译为 allow/deny 规则。"""

from __future__ import annotations

from typing import Any

from log import get_logger
from permission.rules import parse_rule, serialize_rule
from permission.types import PermissionBehavior, PermissionRule

logger = get_logger("permission")


def compile_roles_section(
    section: dict[str, Any],
    *,
    role: str | None = None,
) -> tuple[list[PermissionRule], list[PermissionRule]]:
    """
    从 permission.roles / allow_skills / skill_rules 编译规则。
    返回 (allow_rules, deny_rules)，source=legacy。
    """
    active = role or str(section.get("role", "default"))
    raw_roles = section.get("roles") or {}
    rules: list[PermissionRule] = []

    if isinstance(raw_roles, dict) and raw_roles:
        logger.warning(
            "permission.roles 请改用 permission.rules；"
            f"当前仍按角色 {active!r} 编译"
        )
        body = raw_roles.get(active)
        if body is None:
            logger.warning(f"角色 {active!r} 未定义，legacy 编译为空策略")
            return [], []
        rules.extend(_compile_role_body(body or {}))
        return _split_by_behavior(rules)

    # 简写 allow_skills / skill_rules
    if section.get("allow_skills") or section.get("skill_rules"):
        logger.warning(
            "permission.allow_skills / skill_rules 请改用 permission.rules"
        )
        rules.extend(_compile_legacy_flat(section))
    return _split_by_behavior(rules)


def _split_by_behavior(
    rules: list[PermissionRule],
) -> tuple[list[PermissionRule], list[PermissionRule]]:
    allow: list[PermissionRule] = []
    deny: list[PermissionRule] = []
    for rule in rules:
        if rule.behavior is PermissionBehavior.DENY:
            deny.append(rule)
        else:
            allow.append(rule)
    return allow, deny


def _compile_role_body(body: dict[str, Any]) -> list[PermissionRule]:
    skills_raw = body.get("skills") or {}
    out: list[PermissionRule] = []
    if isinstance(skills_raw, list):
        for name in skills_raw:
            out.append(
                parse_rule(str(name), PermissionBehavior.ALLOW, source="legacy")
            )
        return out
    if not isinstance(skills_raw, dict):
        return out
    for skill_name, rule_body in skills_raw.items():
        out.extend(_compile_skill_entry(str(skill_name), rule_body))
    return out


def _compile_legacy_flat(section: dict[str, Any]) -> list[PermissionRule]:
    out: list[PermissionRule] = []
    allow_skills = section.get("allow_skills")
    if isinstance(allow_skills, list):
        for name in allow_skills:
            out.append(
                parse_rule(str(name), PermissionBehavior.ALLOW, source="legacy")
            )
    skill_rules = section.get("skill_rules") or {}
    if isinstance(skill_rules, dict):
        for name, body in skill_rules.items():
            out.extend(_compile_skill_entry(str(name), body))
    if not out and section.get("default_effect", "deny") == "allow":
        out.append(parse_rule("*", PermissionBehavior.ALLOW, source="legacy"))
    return out


def _compile_skill_entry(skill_name: str, rule_body: Any) -> list[PermissionRule]:
    name = skill_name.strip()
    if rule_body is True or rule_body == "*":
        return [parse_rule(name, PermissionBehavior.ALLOW, source="legacy")]
    if rule_body is False:
        return [
            parse_rule(name, PermissionBehavior.DENY, source="legacy"),
        ]
    if isinstance(rule_body, list):
        return [
            parse_rule(
                serialize_rule(name, str(a).strip().lower()),
                PermissionBehavior.ALLOW,
                source="legacy",
            )
            for a in rule_body
            if str(a).strip()
        ]
    if isinstance(rule_body, dict):
        allow = bool(rule_body.get("allow", True))
        raw_actions = rule_body.get("actions")
        if not allow:
            return [parse_rule(name, PermissionBehavior.DENY, source="legacy")]
        if raw_actions is None or raw_actions == "*" or raw_actions is True:
            return [parse_rule(name, PermissionBehavior.ALLOW, source="legacy")]
        if isinstance(raw_actions, list):
            return [
                parse_rule(
                    serialize_rule(name, str(a).strip().lower()),
                    PermissionBehavior.ALLOW,
                    source="legacy",
                )
                for a in raw_actions
                if str(a).strip()
            ]
    return [parse_rule(name, PermissionBehavior.ALLOW, source="legacy")]
