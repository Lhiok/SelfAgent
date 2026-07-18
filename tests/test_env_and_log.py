from __future__ import annotations

import os

import config as cfg
from env import EnvLayer, get_env
from log import LogLevel, Logger


def test_env_from_config(tmp_path, monkeypatch):
    cfg.set_config({"env": {"DEMO_KEY": "from-config"}})
    monkeypatch.delenv("DEMO_KEY", raising=False)
    assert get_env("DEMO_KEY") == "from-config"


def test_env_from_process(monkeypatch):
    cfg.set_config({})
    monkeypatch.setenv("DEMO_PROCESS", "from-process")
    layer = EnvLayer()
    assert layer.get("DEMO_PROCESS") == "from-process"


def test_env_missing_returns_empty(monkeypatch, capsys):
    cfg.set_config({})
    monkeypatch.delenv("NOT_EXISTS_SA_KEY", raising=False)
    value = get_env("NOT_EXISTS_SA_KEY")
    assert value == ""
    # 严重日志应有输出
    captured = capsys.readouterr()
    assert "未找到环境变量" in captured.err or "未找到环境变量" in captured.out


def test_logger_file_mode(tmp_path):
    log_file = tmp_path / "t.log"
    logger = Logger(level=LogLevel.NOTICE, modes=["file"], file_path=log_file)
    logger.notice("hello")
    logger.warning("warn")
    text = log_file.read_text(encoding="utf-8")
    assert "提醒" in text
    assert "警告" in text
