"""日志层：严重 / 警告 / 提醒，支持控制台、本地文件、服务器上报。"""

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


class LogLevel(IntEnum):
    NOTICE = 10  # 提醒
    WARNING = 20  # 警告
    CRITICAL = 30  # 严重


class LogMode(str):
    CONSOLE = "console"
    FILE = "file"
    SERVER = "server"


_LEVEL_NAMES = {
    LogLevel.NOTICE: "提醒",
    LogLevel.WARNING: "警告",
    LogLevel.CRITICAL: "严重",
}

_LEVEL_ALIASES = {
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
    """多模式日志工具。"""

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
    ) -> None:
        self.name = name
        self.level = _parse_level(level)
        self.modes = list(modes) if modes is not None else [LogMode.CONSOLE]
        self.file_path = Path(file_path) if file_path else None
        self.file_encoding = file_encoding
        self.server_url = server_url or ""
        self.server_timeout = server_timeout
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, name: str = "selfagent") -> "Logger":
        section = cfg.get_section("log", {}) or {}
        file_cfg = section.get("file") or {}
        server_cfg = section.get("server") or {}
        return cls(
            name=name,
            level=section.get("level", LogLevel.NOTICE),
            modes=section.get("modes", [LogMode.CONSOLE]),
            file_path=file_cfg.get("path"),
            file_encoding=file_cfg.get("encoding", "utf-8"),
            server_url=server_cfg.get("url") or "",
            server_timeout=float(server_cfg.get("timeout", 5.0)),
        )

    def notice(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.NOTICE, message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.WARNING, message, **extra)

    def critical(self, message: str, **extra: Any) -> None:
        self.log(LogLevel.CRITICAL, message, **extra)

    # 别名，便于习惯用法
    info = notice
    error = critical

    def log(self, level: LogLevel | str, message: str, **extra: Any) -> None:
        parsed = _parse_level(level)
        if parsed < self.level:
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

    def _write_file(self, line: str) -> None:
        if not self.file_path:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding=self.file_encoding) as f:
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
            # 上报失败时降级到控制台，避免递归依赖 env/log 初始化顺序
            sys.stderr.write(f"[日志上报失败] {exc}\n")
            sys.stderr.flush()


_default_logger: Logger | None = None


def get_logger(name: str = "selfagent") -> Logger:
    global _default_logger
    if _default_logger is None or _default_logger.name != name:
        try:
            _default_logger = Logger.from_config(name=name)
        except Exception:
            _default_logger = Logger(name=name)
    return _default_logger


def reset_logger() -> None:
    global _default_logger
    _default_logger = None
