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

_VALID_STATUS = {"pending", "in_progress", "done", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_todos(root: str | Path) -> dict[str, Any]:
    """供 Web/其它模块读取清单（不经过 Skill.run）。"""
    path = Path(root).resolve() / ".selfagent" / "todos.json"
    if not path.is_file():
        return {"items": [], "updated_at": None, "counts": _counts([])}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": [], "updated_at": None, "counts": _counts([])}
    if not isinstance(data, dict):
        return {"items": [], "updated_at": None, "counts": _counts([])}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    cleaned = [it for it in items if isinstance(it, dict)]
    return {
        "items": cleaned,
        "updated_at": data.get("updated_at"),
        "counts": _counts(cleaned),
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "pending": sum(1 for it in items if it.get("status") == "pending"),
        "in_progress": sum(1 for it in items if it.get("status") == "in_progress"),
        "done": sum(1 for it in items if it.get("status") == "done"),
        "cancelled": sum(1 for it in items if it.get("status") == "cancelled"),
    }


class TodoTrackerSkill(Skill):
    name = "todo_tracker"
    description = (
        "维护任务清单（add/list/start/complete/update/remove/clear），持久化 "
        ".selfagent/todos.json。生命周期必须严格遵守："
        "1) add 拆分待办；2) 真正开始某项时用 start（变为 in_progress）；"
        "3) 该项实际做完后再 complete（不可在开工前 complete）；"
        "4) 同一时间建议只有一项 in_progress。"
        "禁止把 complete 当成「开始做」；开始请用 start + note。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "start", "complete", "remove", "clear", "update"],
                "default": "list",
            },
            "title": {"type": "string", "description": "add/update 时的标题"},
            "id": {
                "type": "string",
                "description": "start/complete/remove/update 的条目 id",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "add 时可一次添加多个标题",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled"],
                "description": "update 时的状态",
            },
            "note": {"type": "string"},
            "force": {
                "type": "boolean",
                "default": False,
                "description": "complete 时若尚未 start，设 true 可强制完成（不推荐）",
            },
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
            return self._ok(items)

        if action == "clear":
            data["items"] = []
            data["updated_at"] = _now()
            self._save(data)
            return self._ok([], message="已清空全部待办")

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
            return self._ok(
                items,
                message=(
                    f"已添加 {len(titles)} 项。下一步：对当前要做的一项调用 "
                    'action=start，再执行实际工作，完成后才 complete。'
                ),
            )

        item_id = str(kwargs.get("id") or "").strip()
        if action in {"start", "complete", "remove", "update"} and not item_id:
            return SkillResult(ok=False, output=f"{action} 需要 id")

        idx = next((i for i, it in enumerate(items) if str(it.get("id")) == item_id), -1)
        if action in {"start", "complete", "remove", "update"} and idx < 0:
            return SkillResult(ok=False, output=f"未找到 id={item_id}")

        if action == "remove":
            removed = items.pop(idx)
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            return self._ok(items, message=f"已删除: {removed.get('title')}")

        if action == "start":
            # 将其余 in_progress 收回 pending，保证同一时间一项进行中
            for i, it in enumerate(items):
                if i != idx and it.get("status") == "in_progress":
                    it["status"] = "pending"
                    it["updated_at"] = _now()
            cur = items[idx]
            if cur.get("status") == "done":
                return SkillResult(
                    ok=False,
                    output=f"条目已完成，无法 start: {cur.get('title')}",
                )
            cur["status"] = "in_progress"
            cur["updated_at"] = _now()
            if kwargs.get("note"):
                cur["note"] = str(kwargs.get("note"))
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            logger.notice(f"todo_tracker start {item_id}")
            return self._ok(
                items,
                message=(
                    f"已开始: {cur.get('title')}。"
                    "请立即用工具执行该项工作；完成后再 complete。"
                ),
            )

        if action == "complete":
            cur = items[idx]
            status = str(cur.get("status") or "pending")
            force = bool(kwargs.get("force"))
            if status == "done":
                return self._ok(items, message=f"已是完成状态: {cur.get('title')}")
            if status == "cancelled":
                return SkillResult(
                    ok=False,
                    output=f"已取消的条目不能 complete: {cur.get('title')}",
                )
            if status == "pending" and not force:
                return SkillResult(
                    ok=False,
                    output=(
                        f"条目仍为 pending，禁止直接 complete: {cur.get('title')}。"
                        "请先 action=start 并完成实际工作；确需跳过流程时传 force=true。"
                    ),
                )
            cur["status"] = "done"
            cur["updated_at"] = _now()
            if kwargs.get("note"):
                cur["note"] = str(kwargs.get("note"))
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            logger.notice(f"todo_tracker complete {item_id}")
            return self._ok(items, message=f"已完成: {cur.get('title')}")

        if action == "update":
            if kwargs.get("title"):
                items[idx]["title"] = str(kwargs.get("title")).strip()
            if kwargs.get("status"):
                st = str(kwargs.get("status")).strip()
                if st not in _VALID_STATUS:
                    return SkillResult(ok=False, output=f"非法 status: {st}")
                items[idx]["status"] = st
            if kwargs.get("note") is not None:
                items[idx]["note"] = str(kwargs.get("note"))
            items[idx]["updated_at"] = _now()
            data["items"] = items
            data["updated_at"] = _now()
            self._save(data)
            return self._ok(items)

        return SkillResult(ok=False, output=f"不支持的 action: {action}")

    def _ok(
        self,
        items: list[dict[str, Any]],
        *,
        message: str | None = None,
    ) -> SkillResult:
        body = self._format(items)
        if message:
            body = f"{message}\n\n{body}"
        return SkillResult(
            ok=True,
            output=body,
            data={"items": items, "counts": _counts(items)},
        )

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
            mark = {
                "done": "✓",
                "cancelled": "×",
                "in_progress": "→",
            }.get(str(it.get("status")), "·")
            note = f" — {it['note']}" if it.get("note") else ""
            lines.append(f"{mark} [{it.get('id')}] {it.get('title')}{note}")
        c = _counts(items)
        lines.append(
            f"合计 {c['total']} · 待办 {c['pending']} · 进行中 {c['in_progress']} · 完成 {c['done']}"
        )
        return "\n".join(lines)
