"""Hooks 配置与执行结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hooks.events import HookEvent
from permission.types import PermissionDecision


@dataclass
class HookDefinition:
    event: HookEvent
    runner: str  # command | http | prompt
    matcher: str = "*"  # tool_name 或 *
    command: str = ""
    url: str = ""
    timeout: float = 30.0
    env: dict[str, str] = field(default_factory=dict)
    # prompt runner：把 stdout 注入上下文
    inject_as: str = "user"  # user | system


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""
    extra_context: str = ""
    permission: PermissionDecision | None = None
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str = "") -> "HookResult":
        return cls(blocked=False, reason=reason, ok=True)

    @classmethod
    def block(cls, reason: str) -> "HookResult":
        return cls(
            blocked=True,
            reason=reason,
            ok=False,
            permission=PermissionDecision.deny(reason),
        )
