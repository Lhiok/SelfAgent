"""完整 Hooks 事件系统。"""

from hooks.events import ALL_HOOK_EVENTS, HookEvent
from hooks.executor import emit, pre_tool_decision
from hooks.registry import (
    HookRegistry,
    get_hook_registry,
    reset_hook_registry,
    set_hook_registry,
)
from hooks.types import HookDefinition, HookResult

__all__ = [
    "ALL_HOOK_EVENTS",
    "HookEvent",
    "HookDefinition",
    "HookResult",
    "HookRegistry",
    "emit",
    "pre_tool_decision",
    "get_hook_registry",
    "set_hook_registry",
    "reset_hook_registry",
]
