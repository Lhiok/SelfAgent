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


class _AskAI(AIClient):
    provider = "scripted"

    def __init__(self) -> None:
        self.n = 0

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        self.n += 1
        if self.n == 1:
            return AIResponse(
                content=(
                    "Thought: 先问用户\n"
                    "Action: ask_user\n"
                    'Action Input: {"question":"选哪个？","options":["方案甲","方案乙"],"allow_custom":false}\n'
                ),
                model="s",
                provider=self.provider,
            )
        return AIResponse(
            content="Thought: 已确认\nFinal Answer: 选了乙\n",
            model="s",
            provider=self.provider,
        )


def _install_agent(store: WorkspaceStore, monkeypatch) -> None:
    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_AskAI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
            max_steps=5,
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)


def test_store_ask_user_bridge(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)
    _install_agent(store, monkeypatch)

    ws = store.add_workspace(str(proj))
    session = store.create_session(ws["id"])
    sid = session["session_id"]

    events: list[dict] = []
    for event in store.iter_chat_events(sid, "帮我选方案"):
        events.append(event)
        if event.get("type") == "ask_user":
            pending = store.get_session(sid).get("pending_ask")
            assert pending and pending["ask_id"] == event["ask_id"]
            store.answer_ask(sid, ask_id=event["ask_id"], raw="2")

    assert any(e.get("type") == "ask_user" for e in events)
    ask = next(e for e in events if e.get("type") == "ask_user")
    assert ask["options"] == ["方案甲", "方案乙"]
    assert len(ask.get("questions") or []) == 1
    assert store.get_session(sid).get("pending_ask") is None

    done = next(e for e in events if e.get("type") == "done")
    assert "选了乙" in (done.get("session") or {}).get("answer", "")

    with pytest.raises(KeyError):
        store.answer_ask(sid, ask_id=ask["ask_id"], raw="1")


def test_store_ask_user_batch(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    class _BatchAI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: 一次问完\n"
                        "Action: ask_user\n"
                        "Action Input: "
                        '{"questions":['
                        '{"question":"A？","options":["a1","a2"]},'
                        '{"question":"B？","options":["b1","b2"]}'
                        "]}\n"
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content="Thought: ok\nFinal Answer: 批量确认完成\n",
                model="s",
                provider=self.provider,
            )

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_BatchAI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
            max_steps=5,
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)
    ws = store.add_workspace(str(proj))
    sid = store.create_session(ws["id"])["session_id"]

    events: list[dict] = []
    for event in store.iter_chat_events(sid, "多问"):
        events.append(event)
        if event.get("type") == "ask_user":
            assert len(event["questions"]) == 2
            store.answer_ask(
                sid,
                ask_id=event["ask_id"],
                answers=[
                    {"id": "1", "raw": "2"},
                    {"id": "2", "raw": "1"},
                ],
            )

    done = next(e for e in events if e.get("type") == "done")
    assert "批量确认完成" in (done.get("session") or {}).get("answer", "")


def test_answer_api_without_pending(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)
    _install_agent(store, monkeypatch)
    client = TestClient(create_app(store=store))

    wid = client.post("/api/workspaces", json={"path": str(proj)}).json()["id"]
    sid = client.post(f"/api/workspaces/{wid}/sessions", json={}).json()["session_id"]

    missing = client.post(
        f"/api/sessions/{sid}/answer",
        json={"ask_id": "nope", "raw": "1"},
    )
    assert missing.status_code == 404


def test_ask_checkpoint_written_during_live_ask(tmp_path, monkeypatch):
    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)
    _install_agent(store, monkeypatch)
    ws = store.add_workspace(str(proj))
    sid = store.create_session(ws["id"])["session_id"]

    ck_path = store._ask_checkpoint_path(sid)
    assert ck_path is not None

    saw_ask = False
    for event in store.iter_chat_events(sid, "帮我选方案"):
        if event.get("type") == "ask_user":
            saw_ask = True
            assert ck_path.is_file()
            data = __import__("json").loads(ck_path.read_text(encoding="utf-8"))
            assert data["ask_id"] == event["ask_id"]
            assert data.get("messages")
            store.answer_ask(sid, ask_id=event["ask_id"], raw="2")
    assert saw_ask
    assert not ck_path.is_file()


def test_orphaned_ask_recovers_after_restart(tmp_path, monkeypatch):
    """模拟进程重启：仅磁盘检查点可恢复作答并续跑（无存活队列）。"""
    import time

    root = tmp_path / "wsroot"
    proj = tmp_path / "code"
    proj.mkdir()
    store = WorkspaceStore(root=root)

    class _ResumeAI(AIClient):
        provider = "scripted"

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            # 续跑时 Observation 已在消息里，直接给出最终答案
            return AIResponse(
                content="Thought: 已确认\nFinal Answer: 选了乙\n",
                model="s",
                provider=self.provider,
            )

    def _fake_agent(workdir: str, *, detail: str | None = None) -> ReActAgent:
        return ReActAgent(
            ai=_ResumeAI(),
            skills=SkillRegistry(permission=PermissionGuard.allow_all()),
            permission=PermissionGuard.allow_all(),
            workdir=workdir,
            detail=detail or "off",
            max_steps=5,
        )

    monkeypatch.setattr(store, "_make_agent", _fake_agent)
    ws = store.add_workspace(str(proj))
    sid = store.create_session(ws["id"])["session_id"]

    ask_id = "deadbeef12ab"
    event = {
        "type": "ask_user",
        "ask_id": ask_id,
        "question": "选哪个？",
        "options": ["方案甲", "方案乙"],
        "questions": [
            {
                "id": "1",
                "question": "选哪个？",
                "options": ["方案甲", "方案乙"],
                "allow_multiple": False,
                "allow_custom": False,
                "default": None,
                "context": "",
            }
        ],
        "allow_multiple": False,
        "allow_custom": False,
        "default": None,
        "context": "",
    }
    checkpoint = {
        "ask_id": ask_id,
        "event": event,
        "mode": "agent",
        "user_task": "帮我选方案",
        "messages": [
            {"role": "system", "content": "sys", "name": None},
            {"role": "user", "content": "帮我选方案", "name": None},
            {
                "role": "assistant",
                "content": (
                    "Thought: 先问用户\n"
                    "Action: ask_user\n"
                    'Action Input: {"question":"选哪个？","options":["方案甲","方案乙"]}\n'
                ),
                "name": None,
            },
        ],
        "created_at": "2026-07-18T00:00:00+08:00",
    }
    store._write_ask_checkpoint(sid, checkpoint)

    # 新进程
    store2 = WorkspaceStore(root=root)
    monkeypatch.setattr(store2, "_make_agent", _fake_agent)
    pending = store2.get_session(sid).get("pending_ask")
    assert pending and pending["ask_id"] == ask_id
    assert pending["options"] == ["方案甲", "方案乙"]

    out = store2.answer_ask(sid, ask_id=ask_id, raw="2")
    assert out.get("resumed") is True

    deadline = time.time() + 5
    while time.time() < deadline:
        s = store2.get_session(sid)
        if (s.get("turn_count") or 0) >= 1 and not s.get("pending_ask"):
            assert "选了乙" in (s.get("turns") or [{}])[-1].get("answer", "")
            ck = store2._ask_checkpoint_path(sid)
            assert ck is None or not ck.is_file()
            return
        time.sleep(0.05)
    raise AssertionError("孤儿 ask 续跑未在超时内完成")
