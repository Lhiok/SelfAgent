"""应用日志：提醒/警告/严重 + 调试通道扇出（参考 Claude Code 多通道模型）。"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Iterable
from urllib import error, request

import config as cfg
from log import debug as debug_mod  # 子模块
from log.errors import reset_error_state
from log.run import ensure_run_log_path, get_run_log_path, reset_run_log_path
from log.sinks import init_sinks


class LogLevel(IntEnum):
    VERBOSE = 5
    DEBUG = 8
    NOTICE = 10  # 提醒 / info
    WARNING = 20  # 警告
    CRITICAL = 30  # 严重 / error


class LogMode(str):
    CONSOLE = "console"
    FILE = "file"
    SERVER = "server"


_LEVEL_NAMES = {
    LogLevel.VERBOSE: "详尽",
    LogLevel.DEBUG: "调试",
    LogLevel.NOTICE: "提醒",
    LogLevel.WARNING: "警告",
    LogLevel.CRITICAL: "严重",
}

_LEVEL_ALIASES = {
    "verbose": LogLevel.VERBOSE,
    "debug": LogLevel.DEBUG,
    "notice": LogLevel.NOTICE,
    "提醒": LogLevel.NOTICE,
    "info": LogLevel.NOTICE,
    "warning": LogLevel.WARNING,
    "warn": LogLevel.WARNING,
    "警告": LogLevel.WARNING,
    "critical": LogLevel.CRITICAL,
    "error": LogLevel.CRITICAL,
    "严重": LogLevel.CRITICAL,
}

_DEBUG_LEVEL_MAP = {
    LogLevel.VERBOSE: "verbose",
    LogLevel.DEBUG: "debug",
    LogLevel.NOTICE: "info",
    LogLevel.WARNING: "warn",
    LogLevel.CRITICAL: "error",
}


def _parse_level(value: Any) -> LogLevel:
    if isinstance(value, LogLevel):
        return value
    if isinstance(value, int):
        return LogLevel(value)
    key = str(value).strip().lower()
    if key not in _LEVEL_ALIASES:
        raise ValueError(f"未知日志等级: {value}")
    return _LEVEL_ALIASES[key]


class Logger:
    """多模式日志工具；开启 debug 时同步写入会话调试文件。"""

    def __init__(
        self,
        *,
        level: LogLevel | str = LogLevel.NOTICE,
        modes: Iterable[str] | None = None,
        file_path: str | Path | None = None,
        file_encoding: str = "utf-8",
        server_url: str | None = None,
        server_timeout: float = 5.0,
        name: str = "selfagent",
        bind_run: bool = False,
    ) -> None:
        self.name = name
        self.level = _parse_level(level)
        self.modes = list(modes) if modes is not None else [LogMode.CONSOLE]
        self.file_path = Path(file_path) if file_path else None
        self.file_encoding = file_encoding
        self.server_url = server_url or ""
        self.server_timeout = server_timeout
        self.bind_run = bind_run
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, name: str = "selfagent") -> "Logger":
        section = cfg.get_section("log", {}) or {}
        file_cfg = section.get("file") or {}
        server_cfg = section.get("server") or {}
        encoding = str(file_cfg.get("encoding") or "utf-8")
        configured = file_cfg.get("path")
        file_path: str | Path | None = None
        bind_run = False
        if configured:
            file_path = ensure_run_log_path(configured, encoding=encoding)
            bind_run = True
        # 调试配置
        debug_cfg = section.get("debug") or {}
        if debug_cfg.get("enabled"):
            debug_mod.enable_debug_logging()
        if debug_cfg.get("session_id"):
            debug_mod.set_session_id(str(debug_cfg["session_id"]))
        if debug_cfg.get("filter"):
            import os

            os.environ.setdefault(
                "SELFAGENT_DEBUG_FILTER", str(debug_cfg.get("filter"))
            )
        if debug_cfg.get("dir"):
            import os

            os.environ.setdefault(
                "SELFAGENT_DEBUG_LOGS_DIR", str(debug_cfg.get("dir"))
            )
        init_sinks()
        return cls(
            name=name,
            level=section.get("level", LogLevel.NOTICE),
            modes=section.get("modes", [LogMode.CONSOLE]),
            file_path=file_path,
            file_encoding=encoding,
            server_url=server_cfg.get("url") or "",
            server_timeout=float(server_cfg.get("timeout", 5.0)),
            bind_run=bind_run,
        )

    def verbose(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.VERBOSE, message, **extra)

    def debug(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.DEBUG, message, **extra)

    def notice(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.NOTICE, message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.WARNING, message, **extra)

    def critical(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.CRITICAL, message, **extra)

    info = notice
    error = critical
    warn = warning

    def log(self, level: LogLevel | str, message: str, **extra: Any) -> None:
        parsed = _parse_level(level)
        if parsed < self.level:
            # 低于应用阈值仍可进 debug 通道（若已开启且等级够）
            self._fanout_debug(parsed, message)
            return
        record = self._build_record(parsed, message, extra)
        line = self._format_line(record)
        with self._lock:
            if LogMode.CONSOLE in self.modes:
                self._write_console(parsed, line)
            if LogMode.FILE in self.modes:
                self._write_file(line)
            if LogMode.SERVER in self.modes:
                self._write_server(record)
        self._fanout_debug(parsed, message)

    def _fanout_debug(self, level: LogLevel, message: str) -> None:
        if not debug_mod.is_debug_mode():
            return
        dbg_level = _DEBUG_LEVEL_MAP.get(level, "debug")
        debug_mod.log_for_debugging(
            message,
            level=dbg_level,  # type: ignore[arg-type]
            category=self.name,
        )

    def _build_record(
        self, level: LogLevel, message: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "level": _LEVEL_NAMES[level],
            "level_code": int(level),
            "logger": self.name,
            "message": message,
            "extra": extra or {},
            "session_id": debug_mod.get_session_id(),
        }

    def _format_line(self, record: dict[str, Any]) -> str:
        extra = record["extra"]
        suffix = f" | {json.dumps(extra, ensure_ascii=False)}" if extra else ""
        return (
            f"[{record['time']}] [{record['level']}] "
            f"[{record['logger']}] {record['message']}{suffix}"
        )

    def _write_console(self, level: LogLevel, line: str) -> None:
        stream = sys.stderr if level >= LogLevel.WARNING else sys.stdout
        stream.write(line + "\n")
        stream.flush()

    def _resolve_file_path(self) -> Path | None:
        if not self.bind_run:
            return self.file_path
        existing = get_run_log_path()
        if existing is not None:
            return existing
        configured = ((cfg.get_section("log", {}) or {}).get("file") or {}).get("path")
        if not configured:
            return None
        return ensure_run_log_path(configured, encoding=self.file_encoding)

    def _write_file(self, line: str) -> None:
        target = self._resolve_file_path()
        if target is None:
            return
        self.file_path = target
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding=self.file_encoding) as f:
            f.write(line + "\n")

    def _write_server(self, record: dict[str, Any]) -> None:
        if not self.server_url:
            return
        payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.server_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.server_timeout) as resp:
                resp.read()
        except error.URLError as exc:
            sys.stderr.write(f"[日志上报失败] {exc}\n")
            sys.stderr.flush()


_default_logger: Logger | None = None
_sinks_ready = False


def get_logger(name: str = "selfagent") -> Logger:
    global _default_logger, _sinks_ready
    if not _sinks_ready:
        try:
            init_sinks()
        except Exception:  # noqa: BLE001
            pass
        _sinks_ready = True
    if _default_logger is None or _default_logger.name != name:
        try:
            _default_logger = Logger.from_config(name=name)
        except Exception:
            _default_logger = Logger(name=name)
    return _default_logger


def reset_logger() -> None:
    global _default_logger, _sinks_ready
    _default_logger = None
    _sinks_ready = False
    reset_run_log_path()
    debug_mod.reset_debug_state()
    reset_error_state()
