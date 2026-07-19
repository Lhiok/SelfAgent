"""会话级调试通道（对齐 claude-code-rev logForDebugging）。"""

from __future__ import annotations

import atexit
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from log.buffer import BufferedWriter
from log.debug_filter import DebugFilter, parse_debug_filter, should_show_debug_message

DebugLevel = Literal["verbose", "debug", "info", "warn", "error"]

_LEVEL_ORDER: dict[DebugLevel, int] = {
    "verbose": 0,
    "debug": 1,
    "info": 2,
    "warn": 3,
    "error": 4,
}

# RLock：get_debug_log_path / _get_writer 会嵌套取锁
_lock = threading.RLock()
_FILTER_UNSET = object()
_runtime_enabled = False
_session_id: str | None = None
_writer: BufferedWriter | None = None
_debug_path: Path | None = None
_filter_cache: DebugFilter | None | object = _FILTER_UNSET

def get_session_id() -> str:
    global _session_id
    with _lock:
        if _session_id:
            return _session_id
        env = (os.environ.get("SELFAGENT_SESSION_ID") or "").strip()
        _session_id = env or uuid.uuid4().hex[:12]
        return _session_id


def set_session_id(session_id: str) -> None:
    """绑定当前 Agent/桌面会话，便于 debug 文件与会话对齐。"""
    global _session_id, _writer, _debug_path
    sid = (session_id or "").strip()
    if not sid:
        return
    with _lock:
        if _session_id == sid:
            return
        if _writer is not None:
            _writer.flush()
            _writer.close()
        _session_id = sid
        _writer = None
        _debug_path = None


def is_debug_mode() -> bool:
    if _runtime_enabled:
        return True
    if _env_truthy("SELFAGENT_DEBUG") or _env_truthy("DEBUG"):
        return True
    if (os.environ.get("SELFAGENT_DEBUG_FILE") or "").strip():
        return True
    return False


def enable_debug_logging() -> bool:
    """运行中打开调试（类似 /debug）。返回此前是否已开启。"""
    global _runtime_enabled
    was = is_debug_mode()
    _runtime_enabled = True
    w = _get_writer()
    if w is not None:
        w.set_immediate(True)
    return was


def get_min_debug_level() -> DebugLevel:
    raw = (os.environ.get("SELFAGENT_DEBUG_LOG_LEVEL") or "debug").strip().lower()
    if raw in _LEVEL_ORDER:
        return raw  # type: ignore[return-value]
    return "debug"


def get_debug_filter() -> DebugFilter | None:
    global _filter_cache
    if _filter_cache is not _FILTER_UNSET:
        return _filter_cache  # type: ignore[return-value]
    spec = (
        os.environ.get("SELFAGENT_DEBUG_FILTER")
        or os.environ.get("SELFAGENT_DEBUG")
        or ""
    )
    # SELFAGENT_DEBUG=1 仅开关；含逗号或字母才当 filter
    if spec.strip() in {"1", "true", "yes", "on"}:
        filt = None
    else:
        filt = parse_debug_filter(spec if any(c in spec for c in ",!") else "")
        if not filt and ":" not in spec and "," not in spec and spec.strip() not in {
            "",
            "1",
            "true",
            "yes",
            "on",
        }:
            # SELFAGENT_DEBUG=api → 当作 include filter
            filt = parse_debug_filter(spec)
    _filter_cache = filt
    return filt


def get_debug_log_path() -> Path:
    global _debug_path
    with _lock:
        if _debug_path is not None:
            return _debug_path
        override = (os.environ.get("SELFAGENT_DEBUG_FILE") or "").strip()
        if override:
            _debug_path = Path(override).expanduser()
            return _debug_path
        base = (os.environ.get("SELFAGENT_DEBUG_LOGS_DIR") or "").strip()
        root = Path(base).expanduser() if base else Path("logs") / "debug"
        _debug_path = root / f"{get_session_id()}.txt"
        return _debug_path


def flush_debug_logs() -> None:
    w = _writer
    if w is not None:
        w.flush()


def log_for_debugging(
    message: str,
    *,
    level: DebugLevel = "debug",
    category: str | None = None,
) -> None:
    """写入会话调试文件；未开启 debug 时默认不落盘。"""
    if not is_debug_mode():
        return
    if _LEVEL_ORDER[level] < _LEVEL_ORDER[get_min_debug_level()]:
        return
    logger_name = category or ""
    if not should_show_debug_message(
        message, get_debug_filter(), logger_name=logger_name
    ):
        return
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
    prefix = f"{category}: " if category else ""
    line = f"{ts} [{level.upper()}] {prefix}{message}"
    writer = _get_writer()
    if writer is None:
        return
    writer.write(line)
    if is_debug_to_stderr():
        import sys

        sys.stderr.write(line + "\n")
        sys.stderr.flush()


def is_debug_to_stderr() -> bool:
    return _env_truthy("SELFAGENT_DEBUG_TO_STDERR")


def _get_writer() -> BufferedWriter | None:
    global _writer
    with _lock:
        if _writer is not None:
            return _writer
        path = get_debug_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            header = (
                f"# SelfAgent debug log\n"
                f"# session={get_session_id()}\n"
                f"# started={datetime.now().astimezone().isoformat(timespec='seconds')}\n"
                f"#\n"
            )
            path.write_text(header, encoding="utf-8")
        _point_latest(path)
        _writer = BufferedWriter(
            path,
            immediate=is_debug_mode(),
            flush_interval=1.0,
        )
        return _writer


def _point_latest(path: Path) -> None:
    """写入 latest 指针；Windows 无权限时退化为 latest.txt。"""
    latest = path.parent / "latest"
    latest_txt = path.parent / "latest.txt"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(path.name)
    except OSError:
        latest_txt.write_text(str(path.resolve()), encoding="utf-8")


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def reset_debug_state() -> None:
    """测试用：重置调试状态。"""
    global _runtime_enabled, _session_id, _writer, _debug_path, _filter_cache
    with _lock:
        if _writer is not None:
            _writer.flush()
            _writer.close()
        _runtime_enabled = False
        _session_id = None
        _writer = None
        _debug_path = None
        _filter_cache = _FILTER_UNSET


atexit.register(flush_debug_logs)
