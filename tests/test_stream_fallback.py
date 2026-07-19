"""流式采样与降级。"""

from ai.base import AIClient, AIMessage, AIResponse, StreamEvent
from permission import PermissionGuard
from session import Agent
from scripted_ai import ScriptedAI, finish, resp
from skills import SkillRegistry


class _BoomStream(AIClient):
    provider = "boom"

    def chat(self, messages, options=None):
        return AIResponse(content="Final Answer: ok", model="m", provider="boom")

    def chat_stream(self, messages, options=None):
        raise RuntimeError("sse broken")
        yield  # pragma: no cover


def test_default_chat_stream_yields_full():
    class _Once(AIClient):
        provider = "x"

        def chat(self, messages, options=None):
            return AIResponse(content="hello", model="m", provider="x")

    events = list(_Once().chat_stream([AIMessage(role="user", content="hi")]))
    assert len(events) == 1
    assert isinstance(events[0], StreamEvent)
    assert events[0].text == "hello"


def test_agent_stream_fallback(monkeypatch):
    agent = Agent(
        ai=_BoomStream(),
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        stream_ai=True,
        max_steps=3,
    )
    result = agent.run("hi")
    assert result.completed
    assert "ok" in result.answer


def test_agent_uses_chat_with_tools_not_stream():
    """有 tools 时 loop 走 chat()；finish 通过 tool_calls 结束。"""
    ai = ScriptedAI([resp(finish("streamed"))])
    agent = Agent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        stream_ai=True,
        max_steps=3,
    )
    result = agent.run("hi")
    assert result.completed
    assert "streamed" in result.answer
    assert ai.n == 1
