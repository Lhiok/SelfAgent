from __future__ import annotations

import json

from permission import PermissionGuard
from session import AgentMode, Agent
from ai.base import AIClient, AIResponse, ChatOptions
from skills import AskUserSkill, LocalFileSkill, SkillRegistry


def test_ask_user_select_by_index():
    skill = AskUserSkill(ask_handler=lambda q, opts, meta: "2")
    result = skill.run(
        question="选哪种日志？",
        options=["仅控制台", "控制台+文件", "全开"],
    )
    assert result.ok
    data = json.loads(result.output)
    assert data["selected"] == ["控制台+文件"]
    assert data["indexes"] == [2]


def test_ask_user_custom_and_default():
    skill = AskUserSkill(ask_handler=lambda q, opts, meta: "")
    result = skill.run(
        question="q",
        options=["A", "B"],
        default="1",
    )
    assert result.ok
    assert json.loads(result.output)["selected"] == ["A"]

    custom = AskUserSkill(ask_handler=lambda q, opts, meta: "我自己的方案")
    result2 = custom.run(question="q", options=["A", "B"], allow_custom=True)
    assert result2.ok
    assert json.loads(result2.output)["selected"] == ["我自己的方案"]


def test_ask_user_batch_questions():
    skill = AskUserSkill(
        ask_handler=lambda q, opts, meta: json.dumps(
            [{"id": "1", "raw": "2"}, {"id": "2", "raw": "1"}],
            ensure_ascii=False,
        )
    )
    result = skill.run(
        questions=[
            {"question": "缓存？", "options": ["内存", "Redis"]},
            {"question": "日志？", "options": ["info", "debug"]},
        ]
    )
    assert result.ok
    data = json.loads(result.output)
    assert data["selected"] == [["Redis"], ["info"]]
    assert len(data["questions"]) == 2


def test_plan_mode_allows_ask_user():
    from scripted_ai import ScriptedAI, resp, submit_plan, tc

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=".", allow_write=False))
    reg.register(AskUserSkill(ask_handler=lambda q, opts, meta: "1"))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    tc(
                        "ask_user",
                        {"question": "选方案", "options": ["A", "B"], "default": "1"},
                        id="ask1",
                    )
                ),
                resp(
                    submit_plan(
                        "采用方案 A",
                        [
                            {
                                "skill": "local_file",
                                "input": {"action": "write", "path": "x.txt", "content": "A"},
                                "why": "按选择写入",
                            }
                        ],
                        thought="按用户选择出计划",
                    )
                ),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.PLAN,
        max_steps=5,
    )
    result = agent.plan("需要确认方案")
    assert result.completed
    assert result.plan.ok
    obs = "\n".join(c.observation or "" for s in result.steps for c in s.calls)
    msgs = "\n".join(m.content or "" for m in result.messages)
    assert '"selected"' in obs or '"selected"' in msgs
    assert "A" in obs or "A" in msgs
