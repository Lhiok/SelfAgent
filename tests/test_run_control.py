"""RunControl 取消与插话。"""

from ai import AIMessage, AIResponse
from permission import PermissionGuard
from react import ReActAgent, RunControl
from skills import SkillRegistry
from skills.base import Skill, SkillResult


class _FakeAI:
    provider = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.stream_calls = 0
        self.chat_calls = 0

    def chat(self, messages, options=None):
        self.calls += 1
        self.chat_calls += 1
        text = self.replies.pop(0) if self.replies else "Final Answer: done"
        return AIResponse(content=text, model="m", provider="fake")


class _SlowStreamAI:
    """流式中可被 cancel 打断；若回落 chat 会记一笔。"""

    provider = "fake-stream"

    def __init__(self) -> None:
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(self, messages, options=None):
        self.chat_calls += 1
        return AIResponse(
            content="Final Answer: should-not-reach", model="m", provider="fake"
        )

    def chat_stream(self, messages, options=None):
        self.stream_calls += 1
        yield "Thought: "
        yield "still thinking"


class _SlowSkill(Skill):
    name = "slow_noop"
    description = "noop"
    parameters_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs):
        return SkillResult(ok=True, output="ok")


def test_cancel_before_step_ends():
    control = RunControl()
    ai = _FakeAI(
        [
            "Thought: x\nAction: slow_noop\nAction Input: {}\n",
            "Thought: y\nFinal Answer: done\n",
        ]
    )
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    skills.register(_SlowSkill())
    agent = ReActAgent(
        ai=ai,
        skills=skills,
        permission=PermissionGuard.allow_all(),
        control=control,
        stream_ai=False,
        max_steps=8,
    )

    def on_progress(ev):
        if ev.get("phase") == "thinking" and ev.get("step") == 1:
            control.cancel()

    agent.on_progress = on_progress
    result = agent.run("task")
    assert result.stop_reason == "cancelled"
    assert result.completed is False


def test_stream_cancel_skips_fallback_chat():
    control = RunControl()
    ai = _SlowStreamAI()
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    agent = ReActAgent(
        ai=ai,
        skills=skills,
        permission=PermissionGuard.allow_all(),
        control=control,
        stream_ai=True,
        max_steps=4,
    )

    def on_progress(ev):
        if ev.get("type") == "assistant_delta":
            control.cancel()

    agent.on_progress = on_progress
    result = agent.run("task")
    assert result.stop_reason == "cancelled"
    assert ai.stream_calls >= 1
    assert ai.chat_calls == 0


def test_enqueue_drains_between_steps():
    control = RunControl()
    ai = _FakeAI(
        [
            "Thought: a\nAction: slow_noop\nAction Input: {}\n",
            "Thought: b\nFinal Answer: finished\n",
        ]
    )
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    skills.register(_SlowSkill())
    agent = ReActAgent(
        ai=ai,
        skills=skills,
        permission=PermissionGuard.allow_all(),
        control=control,
        stream_ai=False,
        max_steps=8,
    )

    def on_progress(ev):
        if ev.get("phase") == "acting" and ev.get("step") == 1:
            control.enqueue("补充：请直接结束")

    agent.on_progress = on_progress
    result = agent.run("task")
    assert result.completed
    assert any(
        "中途补充" in (m.content or "")
        for m in result.messages
        if isinstance(m, AIMessage)
    )
