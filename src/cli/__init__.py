"""SelfAgent 命令行交互（cli 分支）。"""

__all__ = ["main"]


def __getattr__(name: str):
    if name == "main":
        from cli.__main__ import main

        return main
    raise AttributeError(name)
