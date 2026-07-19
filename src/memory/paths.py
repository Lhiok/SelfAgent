"""记忆目录路径与越界校验。"""

from __future__ import annotations

import re
from pathlib import Path


_SAFE_TOPIC = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}$")


def memory_dir(workdir: str | Path, relative: str = ".selfagent/memory") -> Path:
    root = Path(workdir).expanduser().resolve()
    rel = (relative or ".selfagent/memory").strip().replace("\\", "/")
    if rel.startswith("/") or rel.startswith("~") or ".." in Path(rel).parts:
        raise ValueError(f"非法 memory.dir: {relative!r}")
    path = (root / rel).resolve()
    if not _is_under(path, root):
        raise ValueError(f"memory 目录越界: {path}")
    return path


def index_path(mem_root: Path) -> Path:
    return Path(mem_root) / "MEMORY.md"


def topic_path(mem_root: Path, name: str) -> Path:
    raw = (name or "").strip()
    if not raw or ".." in raw or "/" in raw or "\\" in raw:
        raise ValueError(f"非法 topic 名: {name!r}")
    slug = sanitize_topic_name(raw)
    path = (Path(mem_root) / f"{slug}.md").resolve()
    if not _is_under(path, Path(mem_root).resolve()):
        raise ValueError(f"topic 路径越界: {name!r}")
    if path.name.upper() == "MEMORY.MD":
        raise ValueError("topic 名不能为 MEMORY")
    return path


def session_memory_dir(persist_dir: str | Path, session_id: str) -> Path:
    base = Path(persist_dir).expanduser().resolve()
    sid = (session_id or "").strip() or "default"
    # 避免路径穿越
    if "/" in sid or "\\" in sid or ".." in sid:
        sid = re.sub(r"[^\w.-]", "_", sid)[:64]
    return base / sid / "session-memory"


def sanitize_topic_name(name: str) -> str:
    text = (name or "").strip()
    if text.endswith(".md"):
        text = text[:-3]
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w.-]", "_", text, flags=re.UNICODE)
    text = text.strip("._-") or "note"
    if not _SAFE_TOPIC.match(text):
        text = re.sub(r"[^a-zA-Z0-9._-]", "_", text)[:80] or "note"
        if text[0] in ".-":
            text = "n" + text
    return text[:81]


def ensure_under_memory(mem_root: Path, target: Path) -> Path:
    root = Path(mem_root).resolve()
    path = Path(target).resolve()
    if not _is_under(path, root):
        raise ValueError(f"路径不在 memory 目录内: {path}")
    return path


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
