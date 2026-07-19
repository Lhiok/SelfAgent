"""流式采样与降级。"""

from ai.base import AIClient, AIMessage, AIResponse, ChatOptions, StreamEvent


class _BoomStream(AIClient):
    provider = "boom"

    def chat(self, messages, options=None):
        return AIResponse(content="Final Answer: ok", model="m", provider="boom")

    def chat_stream(self, messages, options=None):
        raise RuntimeError("sse broken")
        yield  # pragma: no cover


class _OkStream(AIClient):
    provider = "ok"

    def chat(self, messages, options=None):
        return AIResponse(content="should not use", model="m", provider="ok")

    def chat_stream(self, messages, options=None):
        yield StreamEvent(text="Thought: t\n", done=False)
        yield StreamEvent(text="Final Answer: streamed\n", done=False)


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
    from permission import PermissionGuard
    from react import ReActAgent
    from skills import SkillRegistry

    agent = ReActAgent(
        ai=_BoomStream(),
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        stream_ai=True,
        max_steps=3,
    )
    result = agent.run("hi")
    assert result.completed
    assert "ok" in result.answer


def test_agent_uses_stream_deltas():
    from permission import PermissionGuard
    from react import ReActAgent
    from skills import SkillRegistry

    deltas: list[str] = []

    agent = ReActAgent(
        ai=_OkStream(),
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        stream_ai=True,
        max_steps=3,
        on_progress=lambda e: deltas.append(e.get("delta", ""))
        if e.get("type") == "assistant_delta"
        else None,
    )
    result = agent.run("hi")
    assert result.completed
    assert "streamed" in result.answer
    assert any(deltas)
