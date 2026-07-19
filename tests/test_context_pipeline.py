"""snip / microcompact 管线。"""

from __future__ import annotations

from ai import AIMessage
from agent.context_pipeline import microcompact, prepare_messages, snip_after_boundary
from memory.compact import COMPACT_BOUNDARY


def test_microcompact_clears_old_tool_results() -> None:
    msgs = [
        AIMessage(role="system", content="sys"),
        AIMessage(role="user", content="u"),
    ]
    for i in range(5):
        msgs.append(
            AIMessage(
                role="tool",
                content=f"big-result-{i}" * 10,
                tool_call_id=f"t{i}",
                name="local_file",
            )
        )
    out, changed = microcompact(msgs, keep_recent_tool_results=2)
    assert changed
    tools = [m for m in out if m.role == "tool"]
    assert tools[0].content == "[prior tool result cleared]"
    assert tools[1].content == "[prior tool result cleared]"
    assert "big-result-3" in (tools[3].content or "")
    assert "big-result-4" in (tools[4].content or "")


def test_prepare_messages_snip_and_micro() -> None:
    msgs = [
        AIMessage(role="system", content="base"),
        AIMessage(role="user", content="old"),
        AIMessage(role="system", content=COMPACT_BOUNDARY),
        AIMessage(role="user", content="new"),
        AIMessage(role="tool", content="x" * 100, tool_call_id="a", name="t"),
        AIMessage(role="tool", content="y" * 100, tool_call_id="b", name="t"),
        AIMessage(role="tool", content="keep-me", tool_call_id="c", name="t"),
    ]
    prepared = prepare_messages(msgs, keep_recent_tool_results=1)
    texts = [m.content for m in prepared.messages if m.role == "user"]
    assert "old" not in texts
    assert "new" in texts
    assert prepared.microcompacted
    tools = [m for m in prepared.messages if m.role == "tool"]
    assert tools[-1].content == "keep-me"


def test_snip_keeps_system() -> None:
    msgs = [
        AIMessage(role="system", content="base"),
        AIMessage(role="user", content="a"),
        AIMessage(role="system", content=COMPACT_BOUNDARY),
        AIMessage(role="user", content="b"),
    ]
    out = snip_after_boundary(msgs)
    assert out[0].content == "base"
    assert out[-1].content == "b"
