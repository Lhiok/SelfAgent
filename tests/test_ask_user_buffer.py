from __future__ import annotations

import json

from permission import PermissionGuard
from session import AgentMode, Agent
from scripted_ai import ScriptedAI, resp, submit_plan, tc
from skills import AskUserSkill, LocalFileSkill, SkillRegistry
from skills.ask_user import collect_ask_answers_from_steps


def test_sequential_ask_user_flushed_once_before_final():
    """连续多次 ask_user 先暂存，submit_plan 前一次性询问。"""
    asks: list[dict] = []

    def handler(question, options, meta):
        asks.append({"question": question, "options": options, "meta": meta})
        items = meta.get("questions") or []
        return json.dumps(
            [{"id": str(it.get("id") or i + 1), "raw": "1"} for i, it in enumerate(items)],
            ensure_ascii=False,
        )

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=".", allow_write=False))
    reg.register(AskUserSkill(ask_handler=handler))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    tc(
                        "ask_user",
                        {"question": "缓存？", "options": ["内存", "Redis"]},
                        id="q1",
                    )
                ),
                resp(
                    tc(
                        "ask_user",
                        {"question": "日志？", "options": ["info", "debug"]},
                        id="q2",
                    )
                ),
                resp(
                    submit_plan(
                        "两问均选第一项，计划如下",
                        [
                            {
                                "skill": "local_file",
                                "input": {"action": "read", "path": "a.txt"},
                                "why": "查看",
                            }
                        ],
                        thought="出计划",
                    )
                ),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.PLAN,
        max_steps=8,
    )
    result = agent.plan("需要确认两项")
    assert result.completed
    assert result.plan and result.plan.ok
    # 只应弹出一次（含两题），不应每题弹一次
    assert len(asks) == 1
    assert len(asks[0]["meta"].get("questions") or []) == 2
    assert asks[0]["meta"]["questions"][0]["question"] == "缓存？"
    assert asks[0]["meta"]["questions"][1]["question"] == "日志？"


def test_buffered_ask_flushed_when_model_omits_final_answer_prefix():
    """暂存后模型用纯文本收尾（无 finish/submit_plan）时，仍应弹出 ask_user。"""
    asks: list[dict] = []

    def handler(question, options, meta):
        asks.append({"question": question, "options": options, "meta": meta})
        items = meta.get("questions") or []
        return json.dumps(
            [{"id": str(it.get("id") or i + 1), "raw": "1"} for i, it in enumerate(items)],
            ensure_ascii=False,
        )

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=".", allow_write=False))
    reg.register(AskUserSkill(ask_handler=handler))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    tc(
                        "ask_user",
                        {
                            "questions": [
                                {"question": "P0-1？", "options": ["A", "B"]},
                                {"question": "P0-2？", "options": ["A", "B"]},
                            ]
                        },
                        id="batch",
                    )
                ),
                resp(
                    content=(
                        "两个问题已提交，请在上方选择你偏好的修复方案。"
                        "选择后我会立即生成详细修复计划。"
                    )
                ),
                resp(
                    submit_plan(
                        "计划已就绪",
                        [
                            {
                                "skill": "local_file",
                                "input": {"action": "read", "path": "a.txt"},
                                "why": "查看",
                            }
                        ],
                        thought="根据选择出计划",
                    )
                ),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.PLAN,
        max_steps=8,
    )
    result = agent.plan("重新提问")
    assert result.completed
    assert len(asks) == 1
    assert len(asks[0]["meta"].get("questions") or []) == 2
    assert result.plan and result.plan.ok


def test_collect_ask_answers_from_steps():
    from agent.facade import ActionCall, AgentStep

    step = AgentStep(
        index=1,
        thought="确认",
        calls=[
            ActionCall(
                action="ask_user",
                action_input='{"commit":true}',
                observation='{"questions":[{"id":"1","question":"缓存？","selected":["内存"]}]}',
                ok=True,
                data={
                    "questions": [
                        {
                            "id": "1",
                            "question": "缓存？",
                            "selected": ["内存"],
                            "raw": "1",
                        },
                        {
                            "id": "2",
                            "question": "日志？",
                            "selected": ["info"],
                            "raw": "1",
                        },
                    ]
                },
            )
        ],
    )
    rows = collect_ask_answers_from_steps([step])
    assert len(rows) == 2
    assert rows[0]["question"] == "缓存？"
    assert rows[0]["selected"] == ["内存"]
    assert rows[1]["selected"] == ["info"]
