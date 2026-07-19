"""memory.store 索引与 topic。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory.paths import memory_dir, sanitize_topic_name, topic_path
from memory.store import MemoryStore, parse_frontmatter
from memory.types import MemoryConfig, MemoryType


def test_memory_dir_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        memory_dir(tmp_path, "../outside")


def test_save_and_read_topic(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, config=MemoryConfig())
    ent = store.save_topic(
        "pref-lang",
        "Prefer Chinese replies.",
        description="language",
        mem_type=MemoryType.USER,
    )
    assert ent.meta.name == "pref-lang"
    loaded = store.read_topic("pref-lang")
    assert loaded is not None
    assert "Chinese" in loaded.body
    idx = store.read_index()
    assert "pref-lang" in idx


def test_index_line_cap(tmp_path: Path) -> None:
    cfg = MemoryConfig(max_index_lines=15, max_index_bytes=100000)
    store = MemoryStore(tmp_path, config=cfg)
    for i in range(30):
        store.save_topic(f"t{i}", f"body {i}", description=f"d{i}")
    lines = store.read_index().splitlines()
    assert len(lines) <= 20  # cap + maybe truncation marker


def test_topic_path_safe(tmp_path: Path) -> None:
    root = memory_dir(tmp_path)
    root.mkdir(parents=True)
    with pytest.raises(ValueError):
        topic_path(root, "../evil")
    assert sanitize_topic_name("Hello World") == "Hello-World"


def test_parse_frontmatter() -> None:
    raw = "---\nname: x\ntype: feedback\ndescription: d\n---\nbody here\n"
    meta, body = parse_frontmatter(raw)
    assert meta.name == "x"
    assert meta.type == MemoryType.FEEDBACK
    assert body == "body here"
