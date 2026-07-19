"""文件型记忆层（对齐 Claude Code memdir / session-memory）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config as cfg
from ai import AIMessage
from log import get_logger
from memory.compact import compact_transcript, hard_trim
from memory.extract import MemoryWriteGuard, extract_from_turn
from memory.inject import build_memory_context, build_memory_system_section
from memory.scan import find_relevant
from memory.session import SessionMemory
from memory.settings import compaction_settings
from memory.store import MemoryStore
from memory.types import MemoryConfig, MemoryEntry, MemoryType, TopicMeta

logger = get_logger("memory")


def load_memory_config(
    conversation_cfg: dict[str, Any] | None = None,
) -> MemoryConfig:
    """加载 memory 配置。"""
    mem = dict(cfg.get_section("memory", {}) or {})
    conv = dict(conversation_cfg or {})
    if "compact_after_messages" not in mem and "compact_after_messages" in conv:
        mem["compact_after_messages"] = conv["compact_after_messages"]
    if "compact_keep_recent" not in mem and "compact_keep_recent" in conv:
        mem["compact_keep_recent"] = conv["compact_keep_recent"]
    return MemoryConfig.from_dict(mem)


class MemoryService:
    """门面：store + session notes + inject/compact/extract。"""

    def __init__(
        self,
        workdir: str | Path,
        *,
        session_id: str,
        persist_dir: str | Path | None = None,
        config: MemoryConfig | None = None,
    ) -> None:
        self.config = config or load_memory_config()
        self.workdir = Path(workdir).expanduser().resolve()
        self.session_id = session_id
        self.persist_dir = (
            Path(persist_dir).expanduser().resolve()
            if persist_dir is not None
            else Path("logs/sessions").resolve()
        )
        self.store = MemoryStore(self.workdir, config=self.config)
        self.session = SessionMemory(self.persist_dir, session_id)
        self.guard = MemoryWriteGuard()
        if self.config.enabled:
            try:
                self.store.ensure()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"memory ensure failed: {exc}")

    def set_workdir(self, workdir: str | Path) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.store = MemoryStore(self.workdir, config=self.config)
        if self.config.enabled:
            self.store.ensure()

    def system_section(self) -> str:
        return build_memory_system_section(enabled=self.config.enabled)

    def context_for(self, query: str, *, include_session_notes: bool = False) -> str:
        if not self.config.enabled:
            return ""
        return build_memory_context(
            self.store,
            query,
            config=self.config,
            session=self.session if self.config.session_notes_enabled else None,
            include_session_notes=include_session_notes,
        )

    def compact(
        self,
        messages: list[AIMessage],
        *,
        ai: Any = None,
        use_ai: bool = False,
        compact_after: int | None = None,
        keep_recent: int | None = None,
    ) -> list[AIMessage]:
        after = (
            self.config.compact_after_messages
            if compact_after is None
            else int(compact_after)
        )
        keep = (
            self.config.compact_keep_recent
            if keep_recent is None
            else int(keep_recent)
        )
        if not self.config.enabled:
            return hard_trim(messages, keep_recent=keep)
        return compact_transcript(
            messages,
            session=self.session if self.config.session_notes_enabled else None,
            ai=ai if use_ai else None,
            compact_after=after,
            keep_recent=keep,
            mark_boundary=True,
        )

    def on_turn_end(
        self,
        *,
        user: str,
        answer: str,
        messages: list[AIMessage] | None = None,
    ) -> None:
        if not self.config.enabled:
            return
        try:
            if self.config.session_notes_enabled:
                self.session.update_from_turn(user=user, answer=answer)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"session notes update failed: {exc}")
        try:
            extract_from_turn(
                self.store,
                user=user,
                answer=answer,
                messages=messages,
                config=self.config,
                guard=self.guard,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"extract failed: {exc}")
        finally:
            self.guard.clear()

    def remember(
        self,
        name: str,
        content: str,
        *,
        description: str = "",
        mem_type: MemoryType | str = MemoryType.PROJECT,
    ) -> MemoryEntry:
        ent = self.store.save_topic(
            name, content, description=description, mem_type=mem_type
        )
        self.guard.mark_written()
        return ent

    def list_summary(self) -> dict[str, Any]:
        idx = self.store.read_index() if self.config.enabled else ""
        topics = self.store.list_topics() if self.config.enabled else []
        return {
            "enabled": self.config.enabled,
            "dir": str(self.store.root),
            "index": idx,
            "topics": [t.to_dict() for t in topics],
            "session_notes": self.session.read()
            if self.config.session_notes_enabled
            else "",
            "last_summarized_id": self.session.last_summarized_id,
        }


__all__ = [
    "MemoryService",
    "MemoryStore",
    "MemoryConfig",
    "MemoryEntry",
    "MemoryType",
    "TopicMeta",
    "SessionMemory",
    "MemoryWriteGuard",
    "load_memory_config",
    "build_memory_system_section",
    "build_memory_context",
    "find_relevant",
    "compact_transcript",
    "compaction_settings",
    "hard_trim",
    "extract_from_turn",
]
