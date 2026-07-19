"""记忆类型（对齐 Claude Code memdir taxonomy）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


VALID_TYPES = {t.value for t in MemoryType}


@dataclass
class TopicMeta:
    name: str
    description: str = ""
    type: MemoryType = MemoryType.PROJECT
    path: str = ""

    def to_frontmatter(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.type.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, path: str = "") -> "TopicMeta":
        raw_type = str(data.get("type") or MemoryType.PROJECT.value).lower()
        if raw_type not in VALID_TYPES:
            raw_type = MemoryType.PROJECT.value
        return cls(
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            type=MemoryType(raw_type),
            path=path,
        )


@dataclass
class MemoryEntry:
    meta: TopicMeta
    body: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.meta.name,
            "description": self.meta.description,
            "type": self.meta.type.value,
            "path": self.meta.path,
            "body": self.body,
            "score": self.score,
        }


@dataclass
class MemoryConfig:
    enabled: bool = True
    dir: str = ".selfagent/memory"
    extract_enabled: bool = True
    extract_min_user_chars: int = 20
    max_index_lines: int = 200
    max_index_bytes: int = 25600
    prefetch_top_k: int = 5
    session_notes_enabled: bool = True
    compact_after_messages: int = 32
    compact_keep_recent: int = 12

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MemoryConfig":
        d = dict(data or {})
        return cls(
            enabled=bool(d.get("enabled", True)),
            dir=str(d.get("dir") or ".selfagent/memory"),
            extract_enabled=bool(d.get("extract_enabled", True)),
            extract_min_user_chars=int(d.get("extract_min_user_chars", 20) or 20),
            max_index_lines=int(d.get("max_index_lines", 200) or 200),
            max_index_bytes=int(d.get("max_index_bytes", 25600) or 25600),
            prefetch_top_k=int(d.get("prefetch_top_k", 5) or 5),
            session_notes_enabled=bool(d.get("session_notes_enabled", True)),
            compact_after_messages=int(d.get("compact_after_messages", 32) or 32),
            compact_keep_recent=int(d.get("compact_keep_recent", 12) or 12),
        )
