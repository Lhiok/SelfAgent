"""memory.compact：session notes 优先。"""

from __future__ import annotations

from pathlib import Path

from ai import AIMessage
from memory.compact import COMPACT_BOUNDARY, compact_transcript
from memory.session import SessionMemory


def test_compact_prefers_session_notes(tmp_path: Path) -> None:
    sess = SessionMemory(tmp_path, "sid1")
    sess.write("# Session Memory\n\n## Current State\n重要约束：用 pytest\n")
    msgs = [AIMessage(role="user", content=f"m{i}") for i in range(20)]
    out = compact_transcript(
        msgs,
        session=sess,
        ai=None,
        compact_after=8,
        keep_recent=4,
        mark_boundary=True,
    )
    assert any("[上下文摘要]" in (m.content or "") for m in out)
    assert any("pytest" in (m.content or "") for m in out)
    assert any(COMPACT_BOUNDARY in (m.content or "") for m in out if m.role == "system")
