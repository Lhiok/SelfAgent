from __future__ import annotations

import os

from log import Logger, get_run_log_path, reset_logger
import config as cfg


def test_from_config_uses_per_run_file(tmp_path):
    cfg.set_config(
        {
            "log": {
                "level": "notice",
                "modes": ["file"],
                "file": {"path": str(tmp_path / "app.log")},
            }
        }
    )
    reset_logger()

    a = Logger.from_config("alpha")
    b = Logger.from_config("beta")
    assert a.file_path == b.file_path
    assert a.file_path is not None
    assert a.file_path.parent == tmp_path / "runs"
    assert str(os.getpid()) in a.file_path.name
    assert get_run_log_path() == a.file_path

    a.notice("from-a")
    b.notice("from-b")
    text = a.file_path.read_text(encoding="utf-8")
    assert "from-a" in text and "from-b" in text
    assert not (tmp_path / "app.log").exists()


def test_explicit_file_path_not_rewritten(tmp_path):
    path = tmp_path / "custom.log"
    logger = Logger(modes=["file"], file_path=path)
    logger.notice("direct")
    assert path.read_text(encoding="utf-8").count("direct") == 1
