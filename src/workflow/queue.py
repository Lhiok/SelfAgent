"""优先级消息队列（对齐 Claude Code messageQueueManager）。"""

from __future__ import annotations

import threading
from collections import deque

from workflow.types import JobPriority, QueueItem, QueueMode

_PRIORITY_ORDER = (JobPriority.NOW, JobPriority.NEXT, JobPriority.LATER)


class WorkflowQueue:
    """线程安全：now > next > later。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[JobPriority, deque[QueueItem]] = {
            p: deque() for p in _PRIORITY_ORDER
        }

    def push(
        self,
        text: str,
        *,
        priority: JobPriority | str = JobPriority.NEXT,
        mode: QueueMode | str = QueueMode.PROMPT,
        source: str = "",
        meta: dict | None = None,
    ) -> None:
        msg = (text or "").strip()
        if not msg:
            return
        pri = JobPriority(priority) if isinstance(priority, str) else priority
        md = QueueMode(mode) if isinstance(mode, str) else mode
        item = QueueItem(
            text=msg,
            priority=pri,
            mode=md,
            source=source or "",
            meta=dict(meta or {}),
        )
        with self._lock:
            self._buckets[pri].append(item)

    def drain(
        self,
        *,
        max_items: int = 8,
        max_priority: JobPriority | str = JobPriority.NEXT,
        modes: set[QueueMode] | None = None,
    ) -> list[QueueItem]:
        """取出优先级不超过 max_priority 的项；跳过 mode 不匹配者。"""
        limit = max(0, int(max_items))
        if limit == 0:
            return []
        max_pri = (
            JobPriority(max_priority)
            if isinstance(max_priority, str)
            else max_priority
        )
        allowed = {p for p in _PRIORITY_ORDER if p.rank <= max_pri.rank}
        out: list[QueueItem] = []
        with self._lock:
            for pri in _PRIORITY_ORDER:
                if pri not in allowed:
                    continue
                bucket = self._buckets[pri]
                remaining: list[QueueItem] = []
                while bucket and len(out) < limit:
                    item = bucket.popleft()
                    if modes is not None and item.mode not in modes:
                        remaining.append(item)
                        continue
                    out.append(item)
                for item in remaining:
                    bucket.append(item)
        return out

    def size(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._buckets.values())

    def clear(self) -> None:
        with self._lock:
            for b in self._buckets.values():
                b.clear()


_global_queue: WorkflowQueue | None = None
_global_lock = threading.Lock()


def get_workflow_queue() -> WorkflowQueue:
    global _global_queue
    with _global_lock:
        if _global_queue is None:
            _global_queue = WorkflowQueue()
        return _global_queue


def reset_workflow_queue() -> None:
    """测试用。"""
    global _global_queue
    with _global_lock:
        _global_queue = WorkflowQueue()
