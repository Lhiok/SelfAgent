"""执行匹配的 hooks。"""

from __future__ import annotations

from typing import Any

from hooks.events import HookEvent
from hooks.registry import HookRegistry, get_hook_registry
from hooks.runners import run_hook
from hooks.types import HookResult
from log import get_logger
from permission.types import PermissionDecision

logger = get_logger("hooks.executor")


def emit(
    event: HookEvent | str,
    *,
    tool_name: str | None = None,
    payload: dict[str, Any] | None = None,
    registry: HookRegistry | None = None,
) -> HookResult:
    """
    运行匹配 hooks。任一 command exit 2 → blocked。
    prompt runner 的 stdout 合并到 extra_context。
    """
    ev = HookEvent.parse(event) if not isinstance(event, HookEvent) else event
    if ev is None:
        return HookResult.allow("unknown event")
    reg = registry if registry is not None else get_hook_registry()
    defs = reg.match(ev, tool_name)
    if not defs:
        return HookResult.allow("no hooks")

    base_payload = dict(payload or {})
    base_payload.setdefault("event", ev.value)
    if tool_name:
        base_payload.setdefault("tool_name", tool_name)

    contexts: list[str] = []
    last = HookResult.allow()
    for definition in defs:
        try:
            result = run_hook(definition, base_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"hook 执行异常 [{ev.value}]: {exc}")
            continue
        last = result
        if result.extra_context:
            contexts.append(result.extra_context)
        if result.blocked:
            return result
    if contexts:
        last.extra_context = "\n\n".join(contexts)
    return last


def pre_tool_decision(
    skill: str,
    action: str | None,
    arguments: dict[str, Any],
    *,
    registry: HookRegistry | None = None,
) -> PermissionDecision | None:
    """供 permission pipeline 步骤 3：PreToolUse → deny 或 None。"""
    result = emit(
        HookEvent.PRE_TOOL_USE,
        tool_name=skill,
        payload={
            "skill": skill,
            "action": action,
            "arguments": arguments,
        },
        registry=registry,
    )
    if result.blocked:
        return result.permission or PermissionDecision.deny(
            result.reason or "blocked by PreToolUse hook"
        )
    return None
