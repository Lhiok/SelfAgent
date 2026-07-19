"""预 API 上下文管线：snip → microcompact →（可选）hard_trim。

对齐 Claude Code：boundary 切片 → 就地清空旧 tool result → 长度预算。
完整摘要压缩仍由 memory.compact / Conversation 侧触发。
"""

from __future__ import annotations

from dataclasses import dataclass

from ai import AIMessage
from memory.compact import COMPACT_BOUNDARY, hard_trim

_MICRO_STUB = "[prior tool result cleared]"


@dataclass
class PreparedContext:
    messages: list[AIMessage]
    compacted: bool = False
    microcompacted: bool = False


def prepare_messages(
    messages: list[AIMessage],
    *,
    max_messages: int = 0,
    tool_result_max_chars: int = 12000,
    keep_recent_tool_results: int = 8,
) -> PreparedContext:
    """snip → microcompact → 单条 tool 截断 → 可选 hard_trim。"""
    sliced = snip_after_boundary(messages)
    micro, did_micro = microcompact(
        sliced, keep_recent_tool_results=keep_recent_tool_results
    )
    trimmed = [_trim_tool_content(m, tool_result_max_chars) for m in micro]
    compacted = False
    if max_messages > 0 and len(trimmed) > max_messages:
        system = [trimmed[0]] if trimmed and trimmed[0].role == "system" else []
        rest = trimmed[len(system) :]
        rest = hard_trim(rest, keep_recent=max(4, max_messages - len(system)))
        trimmed = system + rest
        compacted = True
    return PreparedContext(
        messages=trimmed,
        compacted=compacted,
        microcompacted=did_micro,
    )


def snip_after_boundary(messages: list[AIMessage]) -> list[AIMessage]:
    """丢弃 compact boundary 之前的历史（保留首条 system）。"""
    return _slice_after_boundary(messages)


def microcompact(
    messages: list[AIMessage],
    *,
    keep_recent_tool_results: int = 8,
    stub: str = _MICRO_STUB,
) -> tuple[list[AIMessage], bool]:
    """就地清空较旧的 tool 结果正文，保留最近 N 条完整内容。"""
    if keep_recent_tool_results < 0:
        return list(messages), False
    tool_idxs = [i for i, m in enumerate(messages) if m.role == "tool"]
    if len(tool_idxs) <= keep_recent_tool_results:
        return list(messages), False
    clear_set = set(tool_idxs[: len(tool_idxs) - keep_recent_tool_results])
    out: list[AIMessage] = []
    changed = False
    for i, m in enumerate(messages):
        if i in clear_set and (m.content or "") and (m.content or "") != stub:
            out.append(
                AIMessage(
                    role=m.role,
                    content=stub,
                    name=m.name,
                    tool_call_id=m.tool_call_id,
                    tool_calls=list(m.tool_calls),
                )
            )
            changed = True
        else:
            out.append(m)
    return out, changed


def mark_compact_boundary(messages: list[AIMessage]) -> list[AIMessage]:
    """在消息列表末尾插入边界标记（system）。"""
    out = list(messages)
    out.append(AIMessage(role="system", content=COMPACT_BOUNDARY))
    return out


def _slice_after_boundary(messages: list[AIMessage]) -> list[AIMessage]:
    last = -1
    for i, m in enumerate(messages):
        if m.role == "system" and COMPACT_BOUNDARY in (m.content or ""):
            last = i
    if last < 0:
        return list(messages)
    head: list[AIMessage] = []
    if messages and messages[0].role == "system" and COMPACT_BOUNDARY not in (
        messages[0].content or ""
    ):
        head = [messages[0]]
    return head + list(messages[last + 1 :])


def _trim_tool_content(msg: AIMessage, max_chars: int) -> AIMessage:
    if msg.role != "tool" or max_chars <= 0:
        return msg
    text = msg.content or ""
    if len(text) <= max_chars:
        return msg
    clipped = text[:max_chars] + f"\n…(已截断，原长度 {len(text)})"
    return AIMessage(
        role=msg.role,
        content=clipped,
        name=msg.name,
        tool_call_id=msg.tool_call_id,
        tool_calls=list(msg.tool_calls),
    )
