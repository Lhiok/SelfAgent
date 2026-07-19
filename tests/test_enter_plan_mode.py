"""enter_plan_mode → 只读拦截 → submit_plan → confirm。"""

from __future__ import annotations

from pathlib import Path

from permission import PermissionGuard
from plan.types import PlanPhase
from session import Agent, AgentMode, Conversation
from skills import LocalFileSkill, SkillRegistry
from scripted_ai import ScriptedAI, enter_plan, finish, resp, submit_plan, tc


def test_enter_plan_then_submit_and_confirm(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=root, allow_write=True))
    ai = ScriptedAI(
        [
            resp(enter_plan("写文件前先规划")),
            resp(
                tc(
                    "local_file",
                    {"action": "write", "path": "x.txt", "content": "no"},
                    id="w1",
                )
            ),
            resp(
                submit_plan(
                    "写 x.txt",
                    [
                        {
                            "skill": "local_file",
                            "input": {
                                "action": "write",
                                "path": "x.txt",
                                "content": "yes",
                            },
                            "why": "写入",
                        }
                    ],
                )
            ),
        ]
    )
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
        max_steps=8,
        workdir=root,
    )
    conv = Conversation(agent, persist_dir=tmp_path / "sessions")
    result = conv.chat("给项目加文件")
    assert result.plan and result.plan.ok
    assert not (root / "x.txt").exists()
    assert conv.plan_lc.phase is PlanPhase.AWAITING_CONFIRM
    assert agent.mode is AgentMode.PLAN

    done = conv.confirm_plan()
    assert done.completed
    assert (root / "x.txt").read_text(encoding="utf-8") == "yes"


def test_enter_plan_tool_only_in_agent_schemas() -> None:
    from agent.loop import build_schemas_for_mode
    from permission import PermissionGuard
    from skills import SkillRegistry

    reg = SkillRegistry()
    guard = PermissionGuard.allow_all()
    agent_names = {
        s["function"]["name"] for s in build_schemas_for_mode(reg, guard, AgentMode.AGENT)
    }
    plan_names = {
        s["function"]["name"] for s in build_schemas_for_mode(reg, guard, AgentMode.PLAN)
    }
    assert "enter_plan_mode" in agent_names
    assert "submit_plan" not in agent_names
    assert "enter_plan_mode" not in plan_names
    assert "submit_plan" in plan_names
