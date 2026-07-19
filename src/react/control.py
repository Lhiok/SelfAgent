"""运行控制：取消与中途插话队列。"""

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
        with self._lock:
            items = list(self._pending)
            self._pending.clear()
            return items
