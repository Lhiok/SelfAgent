"""错误通道：内存环形缓冲 + 可插拔 sink（对齐 claude-code-rev logError）。"""

from __future__ import annotations

import json
import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from log.debug import get_session_id, log_for_debugging


@dataclass
class ErrorRecord:
    timestamp: str
    message: str
    exc_type: str
    traceback: str
    session_id: str
    extra: dict[str, Any]


class ErrorSink(Protocol):
    def write_error(self, record: ErrorRecord) -> None: ...


_lock = threading.Lock()
_ring: deque[ErrorRecord] = deque(maxlen=100)
_sink: ErrorSink | None = None
_pending: list[ErrorRecord] = []


def attach_error_sink(sink: ErrorSink | None) -> None:
    global _sink
    with _lock:
        _sink = sink
        pending = list(_pending)
        _pending.clear()
    for rec in pending:
        if sink is not None:
            try:
                sink.write_error(rec)
            except Exception:  # noqa: BLE001
                pass


def log_error(exc: BaseException | str, **extra: Any) -> None:
    if isinstance(exc, BaseException):
        message = str(exc) or exc.__class__.__name__
        exc_type = type(exc).__name__
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        message = str(exc)
        exc_type = "Error"
        tb = ""
    record = ErrorRecord(
        timestamp=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        message=message,
        exc_type=exc_type,
        traceback=tb,
        session_id=get_session_id(),
        extra=dict(extra or {}),
    )
    with _lock:
        _ring.append(record)
        sink = _sink
        if sink is None:
            _pending.append(record)
    log_for_debugging(
        f"{exc_type}: {message}",
        level="error",
        category="error",
    )
    if sink is not None:
        try:
            sink.write_error(record)
        except Exception:  # noqa: BLE001
            pass


def get_in_memory_errors(limit: int = 100) -> list[ErrorRecord]:
    with _lock:
        items = list(_ring)
    if limit <= 0:
        return items
    return items[-limit:]


class JsonlErrorSink:
    """按日写入 JSONL（可选持久化）。"""

    def __init__(self, directory: str | Path, *, encoding: str = "utf-8") -> None:
        self.directory = Path(directory)
        self.encoding = encoding
        self.directory.mkdir(parents=True, exist_ok=True)

    def write_error(self, record: ErrorRecord) -> None:
        day = datetime.now().astimezone().strftime("%Y%m%d")
        path = self.directory / f"{day}.jsonl"
        payload = {
            "timestamp": record.timestamp,
            "session_id": record.session_id,
            "exc_type": record.exc_type,
            "message": record.message,
            "traceback": record.traceback,
            "extra": record.extra,
        }
        with path.open("a", encoding=self.encoding) as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def reset_error_state() -> None:
    global _sink, _pending
    with _lock:
        _ring.clear()
        _sink = None
        _pending.clear()
