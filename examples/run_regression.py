"""运行模块回归。"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg
from regression import run_regression


def main() -> None:
    cfg.load_config(ROOT / "config.yaml")
    # 默认离线；需要在线时改为 include_live=True
    report = run_regression(include_live=False)
    print(report.summary())
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
