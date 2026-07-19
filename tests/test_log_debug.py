"""调试通道 / 错误环形缓冲（参考 Claude Code 日志分层）。"""

from __future__ import annotations

import os

from log import (
    LogLevel,
    Logger,
    enable_debug_logging,
    get_debug_log_path,
    get_in_memory_errors,
    is_debug_mode,
    log_error,
    log_for_debugging,
    reset_logger,
    set_session_id,
)
from log.debug_filter import parse_debug_filter, should_show_debug_message


def setup_function():
    reset_logger()
    for k in (
        "SELFAGENT_DEBUG",
        "SELFAGENT_DEBUG_FILTER",
        "SELFAGENT_DEBUG_FILE",
        "SELFAGENT_DEBUG_LOGS_DIR",
        "SELFAGENT_DISABLE_ERROR_FILE",
    ):
        os.environ.pop(k, None)
    os.environ["SELFAGENT_DISABLE_ERROR_FILE"] = "1"


def teardown_function():
    reset_logger()
    os.environ.pop("SELFAGENT_DISABLE_ERROR_FILE", None)


def test_debug_filter_include_exclude():
    filt = parse_debug_filter("api,skills,!feishu")
    assert filt is not None
    assert should_show_debug_message("hello", filt, logger_name="skills.shell_run")
    assert not should_show_debug_message("x", filt, logger_name="feishu")
    assert should_show_debug_message("api: timeout", filt, logger_name="")


def test_session_debug_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SELFAGENT_DEBUG_LOGS_DIR", str(tmp_path / "dbg"))
    monkeypatch.setenv("SELFAGENT_DEBUG", "1")
    set_session_id("sess-test-1")
    assert is_debug_mode()
    enable_debug_logging()
    log_for_debugging("hello-debug", level="debug", category="react")
    path = get_debug_log_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "hello-debug" in text
    assert "REACT" in text.upper() or "react:" in text


def test_logger_fans_out_to_debug(tmp_path, monkeypatch):
    monkeypatch.setenv("SELFAGENT_DEBUG_LOGS_DIR", str(tmp_path / "dbg2"))
    monkeypatch.setenv("SELFAGENT_DEBUG", "1")
    set_session_id("sess-fanout")
    enable_debug_logging()
    logger = Logger(level=LogLevel.NOTICE, modes=["console"], name="skills.local_file")
    logger.notice("patched file")
    text = get_debug_log_path().read_text(encoding="utf-8")
    assert "patched file" in text
    assert "skills.local_file" in text or "skills" in text


def test_error_ring_buffer():
    log_error(ValueError("boom"), where="test")
    log_error("plain-error")
    errs = get_in_memory_errors()
    assert len(errs) >= 2
    assert any("boom" in e.message for e in errs)
    assert any(e.exc_type == "Error" for e in errs)
