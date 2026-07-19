"""memory.extract 互斥与启发式。"""

from __future__ import annotations

from pathlib import Path

from memory.extract import MemoryWriteGuard, extract_from_turn
from memory.store import MemoryStore
from memory.types import MemoryConfig


def test_extract_skips_when_guard_written(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, config=MemoryConfig(extract_min_user_chars=5))
    guard = MemoryWriteGuard()
    guard.mark_written()
    written = extract_from_turn(
        store,
        user="请记住：以后用中文回复",
        answer="好的",
        guard=guard,
    )
    assert written == []


def test_extract_remember_phrase(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, config=MemoryConfig(extract_min_user_chars=5))
    guard = MemoryWriteGuard()
    written = extract_from_turn(
        store,
        user="记住：提交信息用中文",
        answer="已记下",
        guard=guard,
    )
    assert written
    assert store.read_topic(written[0]) is not None
