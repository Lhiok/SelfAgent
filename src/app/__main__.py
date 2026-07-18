"""启动 SelfAgent Windows 桌面应用。"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "未安装 PySide6。请在 app 分支执行:\n"
            "  pip install -e \".[app]\"\n"
            "或: pip install PySide6"
        )
        raise SystemExit(1) from None

    from app.window import MainWindow

    qt_app = QApplication(argv)
    qt_app.setApplicationName("SelfAgent")
    win = MainWindow(config_path=_default_config())
    win.show()
    raise SystemExit(qt_app.exec())


def _default_config() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    path = root / "config.yaml"
    return path if path.is_file() else None


if __name__ == "__main__":
    main()
