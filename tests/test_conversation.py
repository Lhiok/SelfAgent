from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import LocalFileSkill, SkillRegistry


class _MultiTurnAI(AIClient):
    provider = "scripted"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_histories: list[list[str]] = []

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        self.calls += 1
        contents = [m.content for m in messages]
        self.seen_histories.append(contents)
        # 若历史里已有第一轮答案，说明连续对话生效
        joined = "\n".join(contents)
        if "token-alpha" in joined and self.calls >= 2:
            return AIResponse(
                content="Thought: 记得上文\nFinal Answer: recall:token-alpha",
                model="s",
                provider=self.provider,
            )
        return AIResponse(
            content="Thought: 首轮\nFinal Answer: token-alpha",
            model="s",
            provider=self.provider,
        )


def test_conversation_multi_turn_keeps_context():
    ai = _MultiTurnAI()
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=".", allow_write=False))
    agent = ReActAgent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
        max_steps=3,
    )
    conv = Conversation(agent, inject_continuous_hint=True, persist_dir="")

    r1 = conv.chat("记住这个标记")
    assert r1.answer == "token-alpha"
    assert conv.turn_count == 1

    r2 = conv.chat("我刚才让你记住什么？")
    assert r2.answer == "recall:token-alpha"
    assert conv.turn_count == 2
    assert any("token-alpha" in c for c in ai.seen_histories[-1])


def test_conversation_save_load(tmp_path):
    ai = _MultiTurnAI()
    agent = ReActAgent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
    )
    conv = Conversation(agent, persist_dir=tmp_path, inject_continuous_hint=False)
    conv.chat("hi")
    path = conv.save()

    loaded = Conversation.load(path, agent=agent)
    assert loaded.session_id == conv.session_id
    assert loaded.turn_count == 1
    assert loaded.messages


def test_conversation_resume_and_list(tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    ai = _MultiTurnAI()
    agent = ReActAgent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        workdir=work,
    )
    conv = Conversation(agent, persist_dir=tmp_path, inject_continuous_hint=False)
    conv.chat("第一问")
    path = conv.save()
    sid = conv.session_id

    items = Conversation.list_sessions(tmp_path)
    assert len(items) == 1
    assert items[0]["session_id"] == sid
    assert items[0]["turn_count"] == 1

    resolved = Conversation.resolve_session_path("latest", persist_dir=tmp_path)
    assert resolved == path.resolve()
    resolved2 = Conversation.resolve_session_path(sid[:8], persist_dir=tmp_path)
    assert resolved2 == path.resolve()

    agent2 = ReActAgent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        workdir=tmp_path,
    )
    conv2 = Conversation(agent2, persist_dir=tmp_path, inject_continuous_hint=False)
    conv2.resume(path)
    assert conv2.session_id == sid
    assert conv2.turn_count == 1
    assert conv2.agent.workdir == work.resolve()

    # 恢复后继续对话应带着历史
    r = conv2.chat("继续")
    assert conv2.turn_count == 2
    assert r.answer


def test_conversation_reset():
    ai = _MultiTurnAI()
    agent = ReActAgent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
    )
    conv = Conversation(agent, inject_continuous_hint=False, persist_dir="")
    conv.chat("a")
    conv.reset()
    assert conv.turn_count == 0
    assert conv.messages == []
