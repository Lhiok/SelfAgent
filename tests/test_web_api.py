from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import ReActAgent
from skills import SkillRegistry
from web.app import create_app
from web.store import WorkspaceStore


class _AI(AIClient):
    provider = "scripted"

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        return AIResponse(
            content="Thought: ok\nFinal Answer: api-ok\n",
            model="s",
            provider=self.provider,
        )


def test_api_workspace_session_chat(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_AI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)
    client = TestClient(create_app(store=store))

    assert client.get("/api/health").json()["status"] == "ok"

    bad = client.post("/api/workspaces", json={"path": str(tmp_path / "missing")})
    assert bad.status_code == 400

    created = client.post("/api/workspaces", json={"path": str(proj), "title": "Code"})
    assert created.status_code == 200
    wid = created.json()["id"]

    assert client.get(f"/api/workspaces/{wid}/sessions").json() == []

    sess = client.post(f"/api/workspaces/{wid}/sessions", json={"title": "S1"})
    assert sess.status_code == 200
    sid = sess.json()["session_id"]

    chat = client.post(f"/api/sessions/{sid}/chat", json={"message": "列出文件"})
    assert chat.status_code == 200
    body = chat.json()
    assert body["answer"] == "api-ok"
    assert body["turn_count"] == 1

    patched = client.patch(
        f"/api/sessions/{sid}", json={"mode": "plan", "detail": "summary"}
    )
    assert patched.status_code == 200
    assert patched.json()["mode"] == "plan"
    assert patched.json()["detail"] == "summary"

    starred = client.patch(f"/api/sessions/{sid}", json={"starred": True})
    assert starred.status_code == 200
    assert starred.json()["starred"] is True

    archived = client.patch(f"/api/sessions/{sid}", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert client.get(f"/api/workspaces/{wid}/sessions").json() == []
    with_arch = client.get(f"/api/workspaces/{wid}/sessions?include_archived=true")
    assert len(with_arch.json()) == 1

    ws_patch = client.patch(
        f"/api/workspaces/{wid}", json={"collapsed": True, "title": "别名A"}
    )
    assert ws_patch.status_code == 200
    assert ws_patch.json()["collapsed"] is True
    assert ws_patch.json()["title"] == "别名A"

    tree = client.get("/api/workspaces").json()
    assert tree[0]["title"] == "别名A"
    assert tree[0]["sessions"][0]["starred"] is True
    assert tree[0]["archived_count"] == 1

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/workspaces/{wid}/sessions").json() == []


def test_index_page_served(tmp_path):
    client = TestClient(create_app(store=WorkspaceStore(root=tmp_path / "empty")))
    page = client.get("/")
    assert page.status_code == 200
    assert "SelfAgent" in page.text


def test_chat_stream_sse(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_AI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)
    client = TestClient(create_app(store=store))

    wid = client.post("/api/workspaces", json={"path": str(proj)}).json()["id"]
    sid = client.post(f"/api/workspaces/{wid}/sessions", json={}).json()["session_id"]

    res = client.post(
        f"/api/sessions/{sid}/chat/stream",
        json={"message": "你好"},
    )
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    body = res.text
    assert '"type": "status"' in body or '"type":"status"' in body
    assert '"type": "step"' in body or '"type":"step"' in body
    assert '"type": "done"' in body or '"type":"done"' in body
    assert "api-ok" in body


def test_cancel_run_endpoint(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_AI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)
    client = TestClient(create_app(store=store))
    wid = client.post("/api/workspaces", json={"path": str(proj)}).json()["id"]
    sid = client.post(f"/api/workspaces/{wid}/sessions", json={}).json()["session_id"]

    # 先触发一次对话以加载会话
    client.post(f"/api/sessions/{sid}/chat", json={"message": "hi"})
    res = client.post(f"/api/sessions/{sid}/cancel")
    assert res.status_code == 200
    assert res.json().get("cancelled") is True
