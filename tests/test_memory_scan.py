"""memory.scan 相关性打分。"""

from __future__ import annotations

from pathlib import Path

from memory.scan import find_relevant
from memory.store import MemoryStore
from memory.types import MemoryConfig, MemoryType


def test_find_relevant_top_k(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, config=MemoryConfig())
    store.save_topic(
        "lang-zh",
        "Always reply in Chinese.",
        description="language preference",
        mem_type=MemoryType.USER,
    )
    store.save_topic(
        "build-dotnet",
        "Use dotnet test for CI.",
        description="dotnet build",
        mem_type=MemoryType.PROJECT,
    )
    hits = find_relevant(store, "Chinese language preference", top_k=1)
    assert len(hits) == 1
    assert hits[0].meta.name == "lang-zh"
