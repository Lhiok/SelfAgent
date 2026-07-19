"""轻量缓冲写盘（对齐 claude-code-rev bufferedWriter）。"""

from __future__ import annotations

import threading
import time
from pathlib import Path


class BufferedWriter:
    """聚合小写入，定时或强制刷盘；debug 立即模式可关掉缓冲。"""

    def __init__(
        self,
        path: Path,
        *,
        encoding: str = "utf-8",
        flush_interval: float = 1.0,
        immediate: bool = False,
    ) -> None:
        self.path = path
        self.encoding = encoding
        self.flush_interval = max(0.05, flush_interval)
        self.immediate = immediate
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, line: str) -> None:
        if self._closed:
            return
        text = line if line.endswith("\n") else line + "\n"
        with self._lock:
            if self.immediate:
                self._append_disk(text)
                return
            self._buf.append(text)
            if time.monotonic() - self._last_flush >= self.flush_interval:
                self._flush_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._flush_unlocked()

    def close(self) -> None:
        with self._lock:
            self._flush_unlocked()
            self._closed = True

    def set_immediate(self, immediate: bool) -> None:
        with self._lock:
            self.immediate = immediate
            if immediate:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        if not self._buf:
            self._last_flush = time.monotonic()
            return
        data = "".join(self._buf)
        self._buf.clear()
        self._last_flush = time.monotonic()
        self._append_disk(data)

    def _append_disk(self, data: str) -> None:
        with self.path.open("a", encoding=self.encoding) as f:
            f.write(data)
