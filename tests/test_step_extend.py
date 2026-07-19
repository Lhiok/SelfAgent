from __future__ import annotations

from permission import PermissionGuard
from agent.facade import Agent
from scripted_ai import ScriptedAI, finish, resp, tc
from skills import AskUserSkill, SkillRegistry, TodoTrackerSkill


def _agent(tmp_path, ai, ask_raw: str, *, max_steps: int = 2) -> Agent:
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(TodoTrackerSkill(root=tmp_path))
    reg.register(AskUserSkill(ask_handler=lambda q, o, m: ask_raw))
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        max_steps=max_steps,
        mode="agent",
        detail="off",
        workdir=tmp_path,
    )
    agent.max_steps_hard_cap = 20
    agent.step_extend_options = [6, 12]
    return agent


def test_max_steps_asks_and_extends(tmp_path):
    ai = ScriptedAI(
        [
            resp(tc("todo_tracker", {"action": "list"}, id="a")),
            resp(tc("todo_tracker", {"action": "list"}, id="b")),
            resp(finish("加步后续完")),
        ]
    )
    agent = _agent(tmp_path, ai, ask_raw="1")
    result = agent.run("长任务")
    assert result.completed
    assert "加步后续完" in (result.answer or "")
    assert ai.n >= 3
    assert any(c.action == "ask_user" for s in result.steps for c in s.calls)


def test_max_steps_user_declines(tmp_path):
    ai = ScriptedAI(
        [
            resp(tc("todo_tracker", {"action": "list"}, id="a")),
            resp(tc("todo_tracker", {"action": "list"}, id="b")),
            resp(finish("不应到达")),
        ]
    )
    agent = _agent(tmp_path, ai, ask_raw="3")
    result = agent.run("长任务")
    assert not result.completed
    assert "结束" in (result.answer or "") or "上限" in (result.answer or "")
    assert "不应到达" not in (result.answer or "")
    assert ai.n == 2
