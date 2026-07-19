"""定时任务：.selfagent/scheduled_tasks.json + 轮询线程。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from log import get_logger
from workflow.queue import get_workflow_queue
from workflow.types import JobPriority, QueueMode

logger = get_logger("workflow.cron")


def _now_ts() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    t = ts if ts is not None else _now_ts()
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )


@dataclass
class CronTask:
    id: str
    prompt: str
    every_sec: float = 60.0
    enabled: bool = True
    next_fire_at: float = 0.0
    last_fired_at: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "every_sec": self.every_sec,
            "enabled": self.enabled,
            "next_fire_at": self.next_fire_at,
            "last_fired_at": self.last_fired_at,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronTask":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:10]),
            prompt=str(data.get("prompt") or ""),
            every_sec=float(data.get("every_sec") or 60),
            enabled=bool(data.get("enabled", True)),
            next_fire_at=float(data.get("next_fire_at") or 0),
            last_fired_at=(
                float(data["last_fired_at"])
                if data.get("last_fired_at") is not None
                else None
            ),
            meta=dict(data.get("meta") or {})
            if isinstance(data.get("meta"), dict)
            else {},
        )


class CronScheduler:
    """1s 轮询；on_fire → workflow queue（mode=cron）。"""

    def __init__(
        self,
        path: str | Path = ".selfagent/scheduled_tasks.json",
        *,
        poll_interval: float = 1.0,
        clock: Callable[[], float] | None = None,
        on_fire: Callable[[CronTask], None] | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.poll_interval = max(0.05, float(poll_interval))
        self.clock = clock or _now_ts
        self.on_fire = on_fire or self._default_on_fire
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _default_on_fire(self, task: CronTask) -> None:
        get_workflow_queue().push(
            task.prompt,
            priority=JobPriority.NEXT,
            mode=QueueMode.CRON,
            source="workflow.cron",
            meta={"cron_id": task.id, "fired_at": _iso()},
        )

    def load(self) -> list[CronTask]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [CronTask.from_dict(x) for x in items if isinstance(x, dict)]

    def save(self, tasks: list[CronTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _iso(),
            "tasks": [t.to_dict() for t in tasks],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(
        self,
        prompt: str,
        *,
        every_sec: float = 60.0,
        task_id: str | None = None,
    ) -> CronTask:
        with self._lock:
            tasks = self.load()
            now = self.clock()
            task = CronTask(
                id=task_id or uuid.uuid4().hex[:10],
                prompt=prompt.strip(),
                every_sec=max(1.0, float(every_sec)),
                enabled=True,
                next_fire_at=now + max(1.0, float(every_sec)),
            )
            tasks.append(task)
            self.save(tasks)
            return task

    def remove(self, task_id: str) -> bool:
        with self._lock:
            tasks = self.load()
            kept = [t for t in tasks if t.id != task_id]
            if len(kept) == len(tasks):
                return False
            self.save(kept)
            return True

    def tick(self) -> list[CronTask]:
        """处理到期任务（可注入假时钟做单测）。"""
        fired: list[CronTask] = []
        with self._lock:
            tasks = self.load()
            now = self.clock()
            changed = False
            for task in tasks:
                if not task.enabled or not task.prompt.strip():
                    continue
                if task.next_fire_at <= 0:
                    task.next_fire_at = now + task.every_sec
                    changed = True
                    continue
                if now < task.next_fire_at:
                    continue
                try:
                    self.on_fire(task)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"cron on_fire 失败 {task.id}: {exc}")
                task.last_fired_at = now
                task.next_fire_at = now + task.every_sec
                fired.append(task)
                changed = True
            if changed:
                self.save(tasks)
        return fired

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"cron tick 异常: {exc}")
                self._stop.wait(self.poll_interval)

        self._thread = threading.Thread(
            target=_loop, name="workflow-cron", daemon=True
        )
        self._thread.start()
        logger.notice(f"cron 已启动: {self.path}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
