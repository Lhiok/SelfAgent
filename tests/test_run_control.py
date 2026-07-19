"""RunControl 取消与插话。"""

from ai import AIMessage
from permission import PermissionGuard
from session import Agent, RunControl
from scripted_ai import ScriptedAI, finish, resp, tc
from skills import SkillRegistry
from skills.base import Skill, SkillResult


class _SlowSkill(Skill):
    name = "slow_noop"
    description = "noop"
    parameters_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs):
        return SkillResult(ok=True, output="ok")


def test_cancel_before_step_ends():
    control = RunControl()
    ai = ScriptedAI(
        [
            resp(tc("slow_noop", {}, id="s1")),
            resp(finish("done")),
        ]
    )
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    skills.register(_SlowSkill())
    agent = Agent(
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
    """Agent 循环走 chat(tools)；取消后不应再调用 chat 完成回合。"""
    control = RunControl()
    ai = ScriptedAI([resp(finish("should-not-reach"))])
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    agent = Agent(
        ai=ai,
        skills=skills,
        permission=PermissionGuard.allow_all(),
        control=control,
        stream_ai=True,
        max_steps=4,
    )

    def on_progress(ev):
        if ev.get("phase") == "thinking" and ev.get("step") == 1:
            control.cancel()

    agent.on_progress = on_progress
    result = agent.run("task")
    assert result.stop_reason == "cancelled"
    assert ai.n <= 1


def test_enqueue_drains_between_steps():
    control = RunControl()
    ai = ScriptedAI(
        [
            resp(tc("slow_noop", {}, id="s1")),
            resp(finish("finished")),
        ]
    )
    skills = SkillRegistry(permission=PermissionGuard.allow_all())
    skills.register(_SlowSkill())
    agent = Agent(
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
