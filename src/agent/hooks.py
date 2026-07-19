"""Stop / PreTool 钩子兼容层；完整事件见 hooks 包。"""

from __future__ import annotations

from typing import Any, Callable

from agent.types import LoopState
from permission.types import PermissionDecision

StopHook = Callable[[LoopState], None]
# 返回 None 表示放行；返回 PermissionDecision 则短路流水线
PreToolHook = Callable[
    [str, str | None, dict[str, Any]],
    PermissionDecision | None,
]


def run_stop_hooks(
    hooks: list[StopHook] | None,
    state: LoopState,
    *,
    success: bool = True,
) -> None:
    if hooks:
        for hook in hooks:
            try:
                hook(state)
            except Exception:  # noqa: BLE001
                pass
    try:
        from hooks import HookEvent, emit

        emit(
            HookEvent.STOP if success else HookEvent.STOP_FAILURE,
            payload={
                "answer": state.answer,
                "stop_reason": state.stop_reason,
                "completed": state.completed,
                "mode": state.mode.value if state.mode else "",
                "turn_count": state.turn_count,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def run_pre_tool_hooks(
    hooks: list[PreToolHook] | None,
    skill: str,
    *,
    action: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> PermissionDecision | None:
    args = arguments or {}
    # 1) 配置/注册表 PreToolUse（command exit 2 = deny）
    try:
        from hooks.executor import pre_tool_decision

        hooked = pre_tool_decision(skill, action, args)
        if hooked is not None:
            return hooked
    except Exception:  # noqa: BLE001
        pass
    # 2) 程序化 pre_tool_hooks
    if not hooks:
        return None
    for hook in hooks:
        try:
            decision = hook(skill, action, args)
        except Exception:  # noqa: BLE001
            continue
        if decision is not None:
            return decision
    return None
