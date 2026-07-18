"""SelfAgent Windows 桌面工作台（app 分支，原生 PySide6）。"""

from __future__ import annotations

__all__ = ["MainWindow", "WorkspaceStore"]


def __getattr__(name: str):
    if name == "MainWindow":
        from app.window import MainWindow

        return MainWindow
    if name == "WorkspaceStore":
        from app.store import WorkspaceStore

        return WorkspaceStore
    raise AttributeError(name)
