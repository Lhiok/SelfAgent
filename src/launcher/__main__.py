"""python -m launcher / selfagent-app → Electron 桌面（失败回退浏览器）。"""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    start = root / "start.py"
    runpy.run_path(str(start), run_name="__main__")


if __name__ == "__main__":
    main()
