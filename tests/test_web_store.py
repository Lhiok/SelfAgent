from __future__ import annotations

from pathlib import Path

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import ReActAgent
from skills import SkillRegistry
from web.store import WorkspaceStore


class _AI(AIClient):
    provider = "scripted"

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        return AIResponse(
            content="Thought: ok\nFinal Answer: done-from-store\n",
            model="s",
            provider=self.provider,
        )


def test_workspace_and_session_lifecycle(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    proj = tmp_path / "proj"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    ws = store.add_workspace(str(proj), title="Demo")
    assert ws["id"]
    assert Path(ws["path"]) == proj.resolve()
    assert len(store.list_workspaces()) == 1

    # 同路径去重
    again = store.add_workspace(str(proj))
    assert again["id"] == ws["id"]

    # 注入 mock agent 工厂
    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        reg = SkillRegistry(permission=PermissionGuard.allow_all())
        return ReActAgent(
            ai=_AI(),
            skills=reg,
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)

    session = store.create_session(ws["id"], title="任务 A")
    assert session["session_id"]
    assert session["workspace_id"] == ws["id"]
    assert (root / ws["id"] / "sessions" / f"{session['session_id']}.json").is_file()

    listed = store.list_sessions(ws["id"])
    assert len(listed) == 1
    assert listed[0]["title"] == "任务 A"
    assert listed[0]["starred"] is False
    assert listed[0]["archived"] is False

    result = store.chat(session["session_id"], "你好")
    assert result["answer"] == "done-from-store"
    assert result["turn_count"] == 1
    assert len(result["turns"]) == 1

    detail = store.get_session(session["session_id"])
    assert detail["turns"][0]["user"] == "你好"

    store.delete_session(session["session_id"])
    assert store.list_sessions(ws["id"]) == []

    store.remove_workspace(ws["id"])
    assert store.list_workspaces() == []


def test_session_star_archive_and_workspace_collapse(tmp_path, monkeypatch):
    root = tmp_path / "workspaces"
    proj = tmp_path / "proj"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        reg = SkillRegistry(permission=PermissionGuard.allow_all())
        return ReActAgent(
            ai=_AI(),
            skills=reg,
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)

    ws = store.add_workspace(str(proj), title="Demo")
    assert ws["collapsed"] is False

    a = store.create_session(ws["id"], title="普通")
    b = store.create_session(ws["id"], title="收藏")
    c = store.create_session(ws["id"], title="归档")

    store.patch_session(b["session_id"], starred=True)
    store.patch_session(c["session_id"], archived=True)

    active = store.list_sessions(ws["id"], include_archived=False)
    assert [x["title"] for x in active] == ["收藏", "普通"]

    all_sessions = store.list_sessions(ws["id"], include_archived=True)
    assert [x["title"] for x in all_sessions] == ["收藏", "普通", "归档"]
    assert all_sessions[0]["starred"] is True
    assert all_sessions[-1]["archived"] is True

    tree = store.list_workspaces()
    assert len(tree) == 1
    assert tree[0]["session_count"] == 2
    assert tree[0]["archived_count"] == 1
    assert len(tree[0]["sessions"]) == 3

    patched = store.patch_workspace(ws["id"], collapsed=True)
    assert patched["collapsed"] is True
    assert store.get_workspace(ws["id"])["collapsed"] is True

    renamed = store.patch_workspace(ws["id"], title="主项目")
    assert renamed["title"] == "主项目"
    assert store.get_workspace(ws["id"])["title"] == "主项目"
