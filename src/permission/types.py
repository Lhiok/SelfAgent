"""权限类型（对齐 Claude Code PermissionMode / PermissionBehavior）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    BYPASS = "bypass"
    DONT_ASK = "dont_ask"

    @classmethod
    def parse(cls, value: str | PermissionMode | None) -> "PermissionMode":
        if isinstance(value, PermissionMode):
            return value
        raw = (value or "default").strip().lower().replace("-", "_")
        aliases = {
            "acceptedits": cls.ACCEPT_EDITS,
            "accept_edits": cls.ACCEPT_EDITS,
            "bypasspermissions": cls.BYPASS,
            "bypass_permissions": cls.BYPASS,
            "dontask": cls.DONT_ASK,
            "dont_ask": cls.DONT_ASK,
        }
        if raw in aliases:
            return aliases[raw]
        try:
            return cls(raw)
        except ValueError as exc:
            raise ValueError(f"未知权限模式: {value}") from exc


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRule:
    """单条规则：Skill(name) / Skill(name:action) / Skill(prefix:*)。"""

    tool_name: str
    content: str | None  # None / "*" = 全部 action
    behavior: PermissionBehavior
    source: str = "config"  # config | session | legacy
    raw: str = ""

    def key(self) -> str:
        if self.content is None or self.content == "*":
            return self.tool_name
        return f"{self.tool_name}({self.content})"


@dataclass
class PermissionUpdate:
    """会话/持久化建议（对齐 PermissionUpdate）。"""

    behavior: PermissionBehavior
    rule_raw: str
    destination: str = "session"  # session | config

    def to_dict(self) -> dict[str, Any]:
        return {
            "behavior": self.behavior.value,
            "rule": self.rule_raw,
            "destination": self.destination,
        }


@dataclass
class PermissionDecision:
    """三值决策；allowed 仅在 allow 时为 True。"""

    allowed: bool
    reason: str = ""
    behavior: PermissionBehavior = PermissionBehavior.ALLOW
    matched_rule: str = ""
    suggestions: list[PermissionUpdate] = field(default_factory=list)

    @classmethod
    def allow(cls, reason: str = "", *, matched_rule: str = "") -> "PermissionDecision":
        return cls(
            allowed=True,
            reason=reason,
            behavior=PermissionBehavior.ALLOW,
            matched_rule=matched_rule,
        )

    @classmethod
    def deny(
        cls,
        reason: str,
        *,
        matched_rule: str = "",
        suggestions: list[PermissionUpdate] | None = None,
    ) -> "PermissionDecision":
        return cls(
            allowed=False,
            reason=reason,
            behavior=PermissionBehavior.DENY,
            matched_rule=matched_rule,
            suggestions=list(suggestions or []),
        )

    @classmethod
    def ask(
        cls,
        reason: str,
        *,
        matched_rule: str = "",
        suggestions: list[PermissionUpdate] | None = None,
    ) -> "PermissionDecision":
        return cls(
            allowed=False,
            reason=reason,
            behavior=PermissionBehavior.ASK,
            matched_rule=matched_rule,
            suggestions=list(suggestions or []),
        )
