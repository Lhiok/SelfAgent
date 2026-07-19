"""工具分批编排（对齐 CC toolOrchestration.partitionToolCalls）。"""

from __future__ import annotations

from typing import Any, Callable

from ai import ToolCall


def partition_tool_calls(
    calls: list[ToolCall],
    *,
    is_safe: Callable[[str, dict[str, Any] | None], bool],
    parse_args: Callable[[str | None], dict[str, Any]],
    max_concurrency: int = 4,
) -> list[list[ToolCall]]:
    """
    连续 concurrency-safe 工具并入一批（上限 max_concurrency）；
    不安全工具单独成批串行。
    """
    if not calls:
        return []
    batches: list[list[ToolCall]] = []
    current: list[ToolCall] = []
    current_safe: bool | None = None
    cap = max(1, int(max_concurrency))

    for tc in calls:
        args = parse_args(tc.arguments)
        safe = bool(is_safe(tc.name, args))
        if current_safe is None:
            current_safe = safe
            current = [tc]
            continue
        if safe and current_safe and len(current) < cap:
            current.append(tc)
        else:
            batches.append(current)
            current = [tc]
            current_safe = safe
    if current:
        batches.append(current)
    return batches
