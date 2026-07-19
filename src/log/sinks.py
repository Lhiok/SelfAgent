"""启动时挂接日志 sink（对齐 claude-code-rev initSinks）。"""

from __future__ import annotations

import os
from pathlib import Path

from log.errors import JsonlErrorSink, attach_error_sink


def init_sinks() -> None:
    """根据环境/配置挂接错误文件 sink；可重复调用。"""
    raw = (os.environ.get("SELFAGENT_ERROR_LOG_DIR") or "").strip()
    if not raw:
        # 默认开启轻量错误 JSONL
        raw = str(Path("logs") / "errors")
    if (os.environ.get("SELFAGENT_DISABLE_ERROR_FILE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        attach_error_sink(None)
        return
    attach_error_sink(JsonlErrorSink(raw))
