"""一键启动本分支入口：python start.py

也可：selfagent-app / python -m app
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.__main__ import main

if __name__ == "__main__":
    main()
