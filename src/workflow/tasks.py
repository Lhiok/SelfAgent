"""文件 DAG 任务库（对齐 Claude Code Task* / claim）。"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from log import get_logger

logger = get_logger("workflow.tasks")

_VALID = {"pending", "ready", "running", "done", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class TaskNode:
    id: str
    title: str
    status: str = "pending"
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    owner: str = ""
    output: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "blocks": list(self.blocks),
            "blocked_by": list(self.blocked_by),
            "owner": self.owner,
            "output": self.output,
            "meta": dict(self.meta),
            "updated_at": self.updated_at or _now(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskNode":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            status=str(data.get("status") or "pending"),
            blocks=[str(x) for x in (data.get("blocks") or [])],
            blocked_by=[str(x) for x in (data.get("blocked_by") or [])],
            owner=str(data.get("owner") or ""),
            output=str(data.get("output") or ""),
            meta=dict(data.get("meta") or {})
            if isinstance(data.get("meta"), dict)
            else {},
            updated_at=str(data.get("updated_at") or ""),
        )


class TaskStore:
    """按 list_id 分目录；单文件 + .lock 声明式互斥。"""

    def __init__(self, root: str | Path, list_id: str = "default") -> None:
        self.root = Path(root).expanduser().resolve()
        self.list_id = (list_id or "default").strip() or "default"
        self.dir = self.root / self.list_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path = self.dir / "tasks.json"
        self._lock_path = self.dir / "tasks.lock"
        self._mem_lock = threading.Lock()

    def list_tasks(self) -> list[TaskNode]:
        data = self._read()
        return [TaskNode.from_dict(x) for x in data.get("items") or [] if isinstance(x, dict)]

    def get(self, task_id: str) -> TaskNode | None:
        for t in self.list_tasks():
            if t.id == task_id:
                return t
        return None

    def create(
        self,
        title: str,
        *,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TaskNode:
        with self._file_lock():
            data = self._read_unlocked()
            items = list(data.get("items") or [])
            node = TaskNode(
                id=uuid.uuid4().hex[:12],
                title=title.strip(),
                status="pending",
                blocked_by=list(blocked_by or []),
                blocks=list(blocks or []),
                meta=dict(meta or {}),
                updated_at=_now(),
            )
            items.append(node.to_dict())
            self._write_unlocked({"items": items, "updated_at": _now()})
            self._refresh_ready_unlocked()
            return node

    def update(self, task_id: str, **fields: Any) -> TaskNode | None:
        with self._file_lock():
            data = self._read_unlocked()
            items = list(data.get("items") or [])
            found = None
            for i, raw in enumerate(items):
                if not isinstance(raw, dict) or raw.get("id") != task_id:
                    continue
                node = TaskNode.from_dict(raw)
                for k, v in fields.items():
                    if k == "status" and v not in _VALID:
                        continue
                    if hasattr(node, k):
                        setattr(node, k, v)
                node.updated_at = _now()
                items[i] = node.to_dict()
                found = node
                break
            if found is None:
                return None
            self._write_unlocked({"items": items, "updated_at": _now()})
            self._refresh_ready_unlocked()
            return found

    def claim(self, task_id: str, owner: str) -> TaskNode | None:
        """认领 ready/pending 且依赖已满足的任务。"""
        with self._file_lock():
            data = self._read_unlocked()
            items = list(data.get("items") or [])
            by_id = {
                str(x.get("id")): TaskNode.from_dict(x)
                for x in items
                if isinstance(x, dict)
            }
            node = by_id.get(task_id)
            if node is None:
                return None
            if node.status == "running" and node.owner and node.owner != owner:
                return None
            if not self._deps_satisfied(node, by_id):
                return None
            if node.status not in {"pending", "ready", "running"}:
                return None
            node.status = "running"
            node.owner = owner
            node.updated_at = _now()
            by_id[task_id] = node
            self._write_unlocked(
                {
                    "items": [t.to_dict() for t in by_id.values()],
                    "updated_at": _now(),
                }
            )
            return node

    def release(self, task_id: str, *, status: str = "pending", output: str = "") -> TaskNode | None:
        return self.update(
            task_id,
            status=status,
            owner="",
            output=output,
        )

    def ready_tasks(self) -> list[TaskNode]:
        by_id = {t.id: t for t in self.list_tasks()}
        out = []
        for t in by_id.values():
            if t.status in {"pending", "ready"} and self._deps_satisfied(t, by_id):
                out.append(t)
        return out

    def _deps_satisfied(self, node: TaskNode, by_id: dict[str, TaskNode]) -> bool:
        for dep in node.blocked_by:
            d = by_id.get(dep)
            if d is None or d.status != "done":
                return False
        return True

    def _refresh_ready_unlocked(self) -> None:
        data = self._read_unlocked()
        items = list(data.get("items") or [])
        by_id = {
            str(x.get("id")): TaskNode.from_dict(x)
            for x in items
            if isinstance(x, dict)
        }
        changed = False
        for tid, node in by_id.items():
            if node.status == "pending" and self._deps_satisfied(node, by_id):
                node.status = "ready"
                node.updated_at = _now()
                by_id[tid] = node
                changed = True
            elif node.status == "ready" and not self._deps_satisfied(node, by_id):
                node.status = "pending"
                by_id[tid] = node
                changed = True
        if changed:
            self._write_unlocked(
                {
                    "items": [t.to_dict() for t in by_id.values()],
                    "updated_at": _now(),
                }
            )

    def _read(self) -> dict[str, Any]:
        with self._file_lock():
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"items": [], "updated_at": None}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"items": [], "updated_at": None}
        return data if isinstance(data, dict) else {"items": [], "updated_at": None}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _file_lock(self):
        return _FileLock(self._lock_path, self._mem_lock)


class _FileLock:
    def __init__(self, path: Path, mem: threading.Lock) -> None:
        self.path = path
        self.mem = mem
        self._fh = None

    def __enter__(self):
        self.mem.acquire()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+", encoding="utf-8")
        try:
            import msvcrt

            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        except Exception:  # noqa: BLE001
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            except Exception:  # noqa: BLE001
                pass
        return self

    def __exit__(self, *args):
        try:
            if self._fh is not None:
                try:
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:  # noqa: BLE001
                    try:
                        import fcntl

                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                    except Exception:  # noqa: BLE001
                        pass
                self._fh.close()
        finally:
            self.mem.release()
