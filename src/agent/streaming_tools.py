"""流式 tool_calls 收集接口（对齐 StreamingToolExecutor 形状）。

DeepSeek 等提供商在 tools 回合可能仍走 batch chat；本模块先固定
「边收边分批」的接口，供 query_loop 在流式可用时接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from ai import ToolCall


@dataclass
class CollectedToolRound:
    """一轮模型输出中的完整 tool_calls 集合。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finished: bool = False


class StreamingToolCollector:
    """从流式增量中组装 ToolCall；完成后交给 partition + 执行。"""

    def __init__(self) -> None:
        self._content_parts: list[str] = []
        self._by_index: dict[int, dict[str, Any]] = {}

    def feed_delta(
        self,
        *,
        content_delta: str | None = None,
        tool_call_deltas: list[dict[str, Any]] | None = None,
        finish: bool = False,
    ) -> CollectedToolRound | None:
        if content_delta:
            self._content_parts.append(content_delta)
        for d in tool_call_deltas or []:
            idx = int(d.get("index") or 0)
            slot = self._by_index.setdefault(
                idx,
                {"id": "", "name": "", "arguments": ""},
            )
            if d.get("id"):
                slot["id"] = str(d["id"])
            if d.get("name"):
                slot["name"] = str(d["name"])
            if d.get("arguments"):
                slot["arguments"] = str(slot.get("arguments") or "") + str(
                    d["arguments"]
                )
        if not finish:
            return None
        calls = [
            ToolCall(
                id=str(slot.get("id") or f"call_{i}"),
                name=str(slot.get("name") or ""),
                arguments=str(slot.get("arguments") or "{}"),
            )
            for i, slot in sorted(self._by_index.items())
        ]
        return CollectedToolRound(
            content="".join(self._content_parts),
            tool_calls=calls,
            finished=True,
        )

    def from_complete(
        self,
        *,
        content: str = "",
        tool_calls: list[ToolCall] | None = None,
    ) -> CollectedToolRound:
        """batch chat 结果适配为同一形状。"""
        return CollectedToolRound(
            content=content or "",
            tool_calls=list(tool_calls or []),
            finished=True,
        )


def iter_executable_batches(
    round_result: CollectedToolRound,
    partition: Callable[..., list[list[ToolCall]]],
) -> Iterator[list[ToolCall]]:
    for batch in partition(round_result.tool_calls):
        yield batch
