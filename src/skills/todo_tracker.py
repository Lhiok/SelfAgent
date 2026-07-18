"""跨步骤任务清单（落盘到工作目录）。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.todo_tracker")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class TodoTrackerSkill(Skill):
    name = "todo_tracker"
    description = (
        "维护任务清单（add/list/complete/remove/clear），持久化在工作目录 "
        ".selfagent/todos.json，适合多问题修复时跟踪进度。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "complete", "remove", "clear", "update"],
                "default": "list",
            },
            "title": {"type": "string", "description": "add/update 时的标题"},
            "id": {"type": "string", "description": "complete/remove/update 的条目 id"},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "add 时可一次添加多个标题",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "done", "cancelled"],
                "description": "update 时的状态",
            },
            "note": {"type": "string"},
        },
        "required": [],
    }

    def __init__(self, root: str | Path = ".", *, enabled: bool = True) -> None:
        self.root = Path(root).resolve()
        self.enabled = enabled

    @property
    def store_path(self) -> Path:
        return self.root / ".selfagent" / "todos.json"

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="todo_tracker 已在配置中禁用")
        action = str(kwargs.get("action") or "list").strip().lower()
        data = self._load()
        items: list[dict[str, Any]] = list(data.get("items") or [])

        if action == "list":
            return SkillResult(ok=True, output=self._format(items), data={"items": items})

        if action == "clear":
            data["items"] = []
            data["updated_at"] = _now()
            self._save(data)
            return SkillResult(ok=True, output="已清空全部待办", data={"items": []})

        if action == "add":
            titles: list[str] = []
            title = str(kwargs.get("title") or "").strip()
            if title:
                titles.append(title)
            raw_items = kwargs.get("items")
            if isinstance(raw_items, list):
                titles.extend(str(x).strip() for x in raw_items if str(x).strip())
            if not titles:
                return SkillResult(ok=False, output="add 需要 title 或 items")
            for t in titles:
                items.append(
                    {
                        "id": uuid.uuid4().hex[:10],
                        "title": t,
                        "status": "pending",
                        "note": str(kwargs.get("note") or ""),
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                )
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            logger.notice(f"todo_tracker add ×{len(titles)}")
            return SkillResult(ok=True, output=self._format(items), data={"items": items})

        item_id = str(kwargs.get("id") or "").strip()
        if action in {"complete", "remove", "update"} and not item_id:
            return SkillResult(ok=False, output=f"{action} 需要 id")

        idx = next((i for i, it in enumerate(items) if str(it.get("id")) == item_id), -1)
        if action in {"complete", "remove", "update"} and idx < 0:
            return SkillResult(ok=False, output=f"未找到 id={item_id}")

        if action == "remove":
            removed = items.pop(idx)
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            return SkillResult(
                ok=True,
                output=f"已删除: {removed.get('title')}\n\n{self._format(items)}",
                data={"items": items},
            )

        if action == "complete":
            items[idx]["status"] = "done"
            items[idx]["updated_at"] = _now()
            if kwargs.get("note"):
                items[idx]["note"] = str(kwargs.get("note"))
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            return SkillResult(ok=True, output=self._format(items), data={"items": items})

        if action == "update":
            if kwargs.get("title"):
                items[idx]["title"] = str(kwargs.get("title")).strip()
            if kwargs.get("status"):
                items[idx]["status"] = str(kwargs.get("status")).strip()
            if kwargs.get("note") is not None:
                items[idx]["note"] = str(kwargs.get("note"))
            items[idx]["updated_at"] = _now()
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            return SkillResult(ok=True, output=self._format(items), data={"items": items})

        return SkillResult(ok=False, output=f"不支持的 action: {action}")

    def _load(self) -> dict[str, Any]:
        path = self.store_path
        if not path.is_file():
            return {"items": [], "updated_at": _now()}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"items": [], "updated_at": _now()}
        if not isinstance(data, dict):
            return {"items": [], "updated_at": _now()}
        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data

    def _save(self, data: dict[str, Any]) -> None:
        path = self.store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _format(self, items: list[dict[str, Any]]) -> str:
        if not items:
            return "（清单为空）"
        lines = ["=== 任务清单 ==="]
        for it in items:
            mark = {"done": "✓", "cancelled": "×"}.get(str(it.get("status")), "·")
            note = f" — {it['note']}" if it.get("note") else ""
            lines.append(f"{mark} [{it.get('id')}] {it.get('title')}{note}")
        pending = sum(1 for it in items if it.get("status") == "pending")
        done = sum(1 for it in items if it.get("status") == "done")
        lines.append(f"合计 {len(items)} · 待办 {pending} · 完成 {done}")
        return "\n".join(lines)
