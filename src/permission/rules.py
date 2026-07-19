"""规则解析与匹配：Tool(content)（对齐 permissionRuleParser / shellRuleMatching）。"""

from __future__ import annotations

import fnmatch
import re
from typing import Iterable

from permission.types import PermissionBehavior, PermissionRule

_RULE_RE = re.compile(
    r"^(?P<tool>[A-Za-z0-9_.*-]+)(?:\((?P<content>(?:\\.|[^)\\])*)\))?$"
)


def parse_rule(raw: str, behavior: PermissionBehavior, *, source: str = "config") -> PermissionRule:
    text = (raw or "").strip()
    if not text:
        raise ValueError("规则不能为空")
    m = _RULE_RE.match(text)
    if not m:
        raise ValueError(f"无法解析权限规则: {raw}")
    tool = m.group("tool").strip().lower()
    content_raw = m.group("content")
    if content_raw is None:
        content: str | None = None
    else:
        content = _unescape(content_raw).strip().lower() or "*"
    return PermissionRule(
        tool_name=tool,
        content=content,
        behavior=behavior,
        source=source,
        raw=text,
    )


def serialize_rule(tool_name: str, content: str | None = None) -> str:
    tool = (tool_name or "").strip()
    if not tool:
        raise ValueError("tool_name 不能为空")
    if content is None or content == "*":
        return tool
    escaped = content.replace("\\", "\\\\").replace(")", "\\)")
    return f"{tool}({escaped})"


def parse_rule_list(
    items: Iterable[str] | None,
    behavior: PermissionBehavior,
    *,
    source: str = "config",
) -> list[PermissionRule]:
    out: list[PermissionRule] = []
    for item in items or []:
        text = str(item).strip()
        if not text:
            continue
        out.append(parse_rule(text, behavior, source=source))
    return out


def rule_matches(
    rule: PermissionRule,
    skill: str,
    action: str | None,
) -> bool:
    """匹配 skill + action；action 为 None 时仅要求 skill 层命中（列 schema）。"""
    skill_l = (skill or "").strip().lower()
    if not skill_l:
        return False
    if rule.tool_name not in {skill_l, "*"}:
        # Skill(prefix:*) 形式写在 tool_name 上较少见；支持 tool 含 *
        if "*" not in rule.tool_name and not rule.tool_name.endswith(":*"):
            return False
        if rule.tool_name.endswith(":*"):
            if not skill_l.startswith(rule.tool_name[:-2]):
                return False
        elif not fnmatch.fnmatchcase(skill_l, rule.tool_name):
            return False

    if rule.content is None or rule.content == "*":
        return True
    if action is None:
        # 列 schema：有任意针对该 skill 的规则即视为可见
        return True

    op = action.strip().lower()
    content = rule.content
    if content.endswith(":*"):
        return op.startswith(content[:-2])
    if "*" in content or "?" in content:
        return fnmatch.fnmatchcase(op, content)
    return op == content


def any_rule_matches(
    rules: Iterable[PermissionRule],
    skill: str,
    action: str | None,
) -> PermissionRule | None:
    for rule in rules:
        if rule_matches(rule, skill, action):
            return rule
    return None


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)
