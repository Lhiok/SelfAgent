"""memory_ops Skill：list/read/save/search（写仅限 memdir）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from memory.scan import find_relevant
from memory.store import MemoryStore
from memory.types import MemoryConfig, MemoryType, VALID_TYPES
from skills.base import Skill, SkillResult


class MemoryOpsSkill(Skill):
    name = "memory_ops"
    description = (
        "读写持久记忆（.selfagent/memory）。"
        "action=list/read/save/search。"
        "save 时 type=user|feedback|project|reference；细节写 topic，索引自动更新。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "save", "search"],
                "default": "list",
            },
            "name": {"type": "string", "description": "topic 名（read/save）"},
            "content": {"type": "string", "description": "save 正文"},
            "description": {"type": "string"},
            "type": {
                "type": "string",
                "enum": sorted(VALID_TYPES),
                "default": "project",
            },
            "query": {"type": "string", "description": "search 关键词"},
        },
        "required": [],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        config: MemoryConfig | None = None,
        on_write: Callable[[], None] | None = None,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.config = config or MemoryConfig()
        self.on_write = on_write
        self.enabled = enabled
        self._store = MemoryStore(self.root, config=self.config)

    def set_workdir(self, workdir: str | Path) -> None:
        self.root = Path(workdir).resolve()
        self._store = MemoryStore(self.root, config=self.config)

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled or not self.config.enabled:
            return SkillResult(ok=False, output="memory 已禁用")
        action = str(kwargs.get("action") or "list").strip().lower()
        store = self._store
        store.ensure()

        if action == "list":
            topics = store.list_topics()
            if not topics:
                return SkillResult(ok=True, output="（记忆为空）", data={"items": []})
            lines = ["=== Memory topics ==="]
            for t in topics:
                lines.append(
                    f"- {t.meta.name} [{t.meta.type.value}] {t.meta.description}"
                )
            return SkillResult(
                ok=True,
                output="\n".join(lines),
                data={"items": [t.to_dict() for t in topics]},
            )

        if action == "read":
            name = str(kwargs.get("name") or "").strip()
            if not name:
                return SkillResult(ok=False, output="read 需要 name")
            ent = store.read_topic(name)
            if ent is None:
                return SkillResult(ok=False, output=f"未找到 topic: {name}")
            return SkillResult(
                ok=True,
                output=f"# {ent.meta.name}\n{ent.meta.description}\n\n{ent.body}",
                data=ent.to_dict(),
            )

        if action == "search":
            query = str(kwargs.get("query") or kwargs.get("name") or "").strip()
            hits = find_relevant(store, query, top_k=self.config.prefetch_top_k)
            if not hits:
                return SkillResult(ok=True, output="（无匹配）", data={"items": []})
            lines = [f"=== search: {query} ==="]
            for h in hits:
                lines.append(f"- {h.meta.name} (score={h.score:.1f}) {h.meta.description}")
            return SkillResult(
                ok=True,
                output="\n".join(lines),
                data={"items": [h.to_dict() for h in hits]},
            )

        if action == "save":
            name = str(kwargs.get("name") or "").strip()
            content = str(kwargs.get("content") or "").strip()
            if not name or not content:
                return SkillResult(ok=False, output="save 需要 name 与 content")
            raw_type = str(kwargs.get("type") or "project").lower()
            if raw_type not in VALID_TYPES:
                raw_type = "project"
            ent = store.save_topic(
                name,
                content,
                description=str(kwargs.get("description") or ""),
                mem_type=MemoryType(raw_type),
            )
            if self.on_write is not None:
                try:
                    self.on_write()
                except Exception:  # noqa: BLE001
                    pass
            return SkillResult(
                ok=True,
                output=f"已保存记忆 topic: {ent.meta.name}",
                data=ent.to_dict(),
            )

        return SkillResult(ok=False, output=f"不支持的 action: {action}")
