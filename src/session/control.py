"""运行控制：取消与中途插话队列（桥接 workflow.queue）。"""

from __future__ import annotations

import threading
from collections import deque


class RunControl:
    """线程安全的本轮运行控制。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = False
        self._pending: deque[str] = deque()
        self._running = False
        self.bridge_workflow_queue: bool = True

    def begin_run(self) -> None:
        with self._lock:
            self._cancel = False
            self._running = True

    def end_run(self) -> None:
        with self._lock:
            self._running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel

    def cancel(self) -> None:
        with self._lock:
            self._cancel = True

    def clear_cancel(self) -> None:
        with self._lock:
            self._cancel = False

    def enqueue(self, text: str) -> None:
        msg = (text or "").strip()
        if not msg:
            return
        with self._lock:
            self._pending.append(msg)

    def drain_pending(self) -> list[str]:
        """本地插话 + workflow 队列通知（notification/cron/外部 prompt）。"""
        with self._lock:
            local = list(self._pending)
            self._pending.clear()
        if not self.bridge_workflow_queue:
            return local
        try:
            from config import get_section

            cfg = get_section("workflow", {}) or {}
            max_items = int(cfg.get("queue_drain_per_turn", 4))
        except Exception:  # noqa: BLE001
            max_items = 4
        # 延迟导入，避免与 workflow 包循环依赖
        from workflow.queue import get_workflow_queue
        from workflow.types import JobPriority, QueueMode

        items = get_workflow_queue().drain(
            max_items=max_items,
            max_priority=JobPriority.NEXT,
            modes={QueueMode.PROMPT, QueueMode.NOTIFICATION, QueueMode.CRON},
        )
        seen = set(local)
        out = list(local)
        for it in items:
            if it.text not in seen:
                seen.add(it.text)
                out.append(it.text)
        return out
