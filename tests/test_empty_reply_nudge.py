from __future__ import annotations

from permission import PermissionGuard
from agent.facade import Agent
from scripted_ai import ScriptedAI, finish, resp, tc
from skills import SkillRegistry, TodoTrackerSkill


def test_empty_reply_is_nudged_then_continues(tmp_path):
    ai = ScriptedAI(
        [
            resp(),  # empty
            resp(tc("todo_tracker", {"action": "list"}, id="t1")),
            resp(finish("已列出清单")),
        ]
    )
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(TodoTrackerSkill(root=tmp_path))
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        max_steps=6,
        mode="agent",
        detail="off",
        workdir=tmp_path,
    )
    result = agent.run("列出清单")
    assert result.completed
    assert "已列出清单" in (result.answer or "")
    assert ai.n >= 3


def test_empty_final_answer_is_nudged(tmp_path):
    ai = ScriptedAI(
        [
            resp(),  # empty → nudge
            resp(finish("真正结束")),
        ]
    )
    agent = Agent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        max_steps=5,
        mode="agent",
        detail="off",
        workdir=tmp_path,
    )
    result = agent.run("随便")
    assert result.completed
    assert "真正结束" in (result.answer or "")
