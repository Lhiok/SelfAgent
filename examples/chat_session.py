"""兼容入口：转发到 cli 包。请优先使用 `selfagent-cli` 或 `python -m cli`。"""

from __future__ import annotations

from cli.__main__ import main

if __name__ == "__main__":
    main()
