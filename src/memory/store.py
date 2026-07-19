"""MEMORY.md 索引 + topic 文件读写。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from log import get_logger
from memory.paths import ensure_under_memory, index_path, memory_dir, topic_path
from memory.types import MemoryConfig, MemoryEntry, MemoryType, TopicMeta, VALID_TYPES

logger = get_logger("memory.store")

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|bearer)\s*[:=]\s*\S+"
)


class MemoryStore:
    def __init__(
        self,
        workdir: str | Path,
        *,
        config: MemoryConfig | None = None,
        relative_dir: str | None = None,
    ) -> None:
        self.config = config or MemoryConfig()
        rel = relative_dir if relative_dir is not None else self.config.dir
        self.workdir = Path(workdir).expanduser().resolve()
        self.root = memory_dir(self.workdir, rel)

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        idx = index_path(self.root)
        if not idx.is_file():
            idx.write_text(
                "# Memory Index\n\n"
                "> 一行一条指针：`- [title](file.md) — 简述`\n\n",
                encoding="utf-8",
            )
        return self.root

    def read_index(self) -> str:
        self.ensure()
        path = index_path(self.root)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_index(self, text: str) -> None:
        self.ensure()
        clipped = self._clip_index(text or "")
        index_path(self.root).write_text(clipped, encoding="utf-8")

    def list_topics(self) -> list[MemoryEntry]:
        self.ensure()
        out: list[MemoryEntry] = []
        for path in sorted(self.root.glob("*.md")):
            if path.name.upper() == "MEMORY.MD":
                continue
            entry = self.read_topic(path.stem)
            if entry is not None:
                out.append(entry)
        return out

    def read_topic(self, name: str) -> MemoryEntry | None:
        try:
            path = topic_path(self.root, name)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, body = parse_frontmatter(raw, default_name=path.stem)
        meta.path = str(path.relative_to(self.root)).replace("\\", "/")
        return MemoryEntry(meta=meta, body=body)

    def save_topic(
        self,
        name: str,
        body: str,
        *,
        description: str = "",
        mem_type: MemoryType | str = MemoryType.PROJECT,
        update_index: bool = True,
    ) -> MemoryEntry:
        self.ensure()
        path = topic_path(self.root, name)
        ensure_under_memory(self.root, path)
        mt = (
            mem_type
            if isinstance(mem_type, MemoryType)
            else MemoryType(
                str(mem_type).lower()
                if str(mem_type).lower() in VALID_TYPES
                else MemoryType.PROJECT.value
            )
        )
        cleaned = filter_secrets(body or "")
        meta = TopicMeta(
            name=path.stem,
            description=(description or cleaned.strip().split("\n", 1)[0])[:200],
            type=mt,
            path=path.name,
        )
        content = render_topic(meta, cleaned)
        path.write_text(content, encoding="utf-8")
        if update_index:
            self._upsert_index_pointer(meta)
        logger.notice(f"memory topic saved: {path.name}")
        return MemoryEntry(meta=meta, body=cleaned)

    def delete_topic(self, name: str) -> bool:
        try:
            path = topic_path(self.root, name)
        except ValueError:
            return False
        if not path.is_file():
            return False
        path.unlink()
        self._remove_index_pointer(path.name)
        return True

    def _upsert_index_pointer(self, meta: TopicMeta) -> None:
        idx = self.read_index()
        lines = idx.splitlines()
        pointer = f"- [{meta.name}]({meta.path or (meta.name + '.md')}) — {meta.description}"
        key = f"]({meta.path or (meta.name + '.md')})"
        new_lines: list[str] = []
        replaced = False
        for line in lines:
            if key in line and line.strip().startswith("-"):
                new_lines.append(pointer)
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(pointer)
        self.write_index("\n".join(new_lines) + "\n")

    def _remove_index_pointer(self, filename: str) -> None:
        idx = self.read_index()
        lines = [
            ln
            for ln in idx.splitlines()
            if f"]({filename})" not in ln
        ]
        self.write_index("\n".join(lines) + ("\n" if lines else ""))

    def _clip_index(self, text: str) -> str:
        lines = text.splitlines()
        max_lines = max(10, self.config.max_index_lines)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(f"\n<!-- truncated: max {max_lines} lines -->")
        out = "\n".join(lines)
        if not out.endswith("\n"):
            out += "\n"
        max_bytes = max(1024, self.config.max_index_bytes)
        raw = out.encode("utf-8")
        if len(raw) > max_bytes:
            out = raw[:max_bytes].decode("utf-8", errors="ignore")
            out += "\n<!-- truncated: max bytes -->\n"
        return out


def parse_frontmatter(raw: str, *, default_name: str = "") -> tuple[TopicMeta, str]:
    text = raw or ""
    m = _FM_RE.match(text)
    if not m:
        return TopicMeta(name=default_name or "note"), text.strip()
    fm_block, body = m.group(1), m.group(2)
    data: dict[str, Any] = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        data[k.strip().lower()] = v.strip().strip("\"'")
    if not data.get("name"):
        data["name"] = default_name
    return TopicMeta.from_dict(data), body.strip()


def render_topic(meta: TopicMeta, body: str) -> str:
    fm = meta.to_frontmatter()
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append((body or "").rstrip())
    lines.append("")
    return "\n".join(lines)


def filter_secrets(text: str) -> str:
    out_lines = []
    for line in (text or "").splitlines():
        if _SECRET_RE.search(line):
            out_lines.append("<!-- redacted secret-like line -->")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
