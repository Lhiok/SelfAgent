"""SelfAgent 本地对话工作台（FastAPI + 静态 SPA）。"""

__all__ = ["create_app", "WorkspaceStore"]


def __getattr__(name: str):
    if name == "create_app":
        from web.app import create_app

        return create_app
    if name == "WorkspaceStore":
        from web.store import WorkspaceStore

        return WorkspaceStore
    raise AttributeError(name)
