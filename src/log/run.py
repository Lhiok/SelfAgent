"""本次进程运行的独立日志文件（不同运行实例互不混写）。"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

_lock = threading.Lock()
_run_log_path: Path | None = None


def get_run_log_path() -> Path | None:
    return _run_log_path


def ensure_run_log_path(configured: str | Path, *, encoding: str = "utf-8") -> Path:
    """
    按配置中的 path 推导本进程专用日志路径，全进程只创建一次。

    例：logs/app.log → logs/runs/20260718_155731_12345.log
    """
    global _run_log_path
    with _lock:
        if _run_log_path is not None:
            return _run_log_path

        base = Path(configured).expanduser()
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        pid = os.getpid()
        path = base.parent / "runs" / f"{stamp}_{pid}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# SelfAgent run log\n"
            f"# time={datetime.now().astimezone().isoformat(timespec='seconds')}\n"
            f"# pid={pid}\n"
            f"#\n"
        )
        with path.open("a", encoding=encoding) as f:
            f.write(header)
        _run_log_path = path
        return path


def reset_run_log_path() -> None:
    global _run_log_path
    with _lock:
        _run_log_path = None
