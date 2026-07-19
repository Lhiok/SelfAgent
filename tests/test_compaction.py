"""上下文压缩。"""

from ai import AIMessage
from react.compaction import compact_messages, hard_trim


def test_hard_trim_keeps_recent():
    msgs = [AIMessage(role="user", content=f"u{i}") for i in range(10)]
    out = hard_trim(msgs, keep_recent=3)
    assert len(out) == 3
    assert out[0].content == "u7"


def test_compact_without_ai_falls_back():
    msgs = [AIMessage(role="user", content=f"msg-{i}") for i in range(20)]
    out = compact_messages(msgs, ai=None, compact_after=8, keep_recent=4)
    assert any("[上下文摘要]" in (m.content or "") for m in out)
    assert len(out) == 5  # summary + 4 recent
