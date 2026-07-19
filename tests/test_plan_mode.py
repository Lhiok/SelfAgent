from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from session import AgentMode, Plan, PlanStep, Agent
from plan.model import clean_plan_summary, looks_like_choice_prompt
from skills import LocalFileSkill, SkillRegistry


class _NoAI(AIClient):
    provider = "none"

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        raise RuntimeError("不应调用模型")


def test_clean_summary_strips_choice_prompt_tables():
    quiz = """
| 问题 | 方案 |
| --- | --- |
| 问题6 | A: xxx |
| 问题8 | B: yyy |

请回复你的选择：6:B, 8:A
"""
    assert looks_like_choice_prompt(quiz)
    assert clean_plan_summary(quiz, has_steps=True) == "计划已就绪，请确认后执行。"


def test_plan_mode_final_answer_without_skill_plan_is_shown_as_answer():
    """无 skill= Plan 的 finish 回答（含是否同意话术）直接作为回答展示，不弹 ask_user。"""
    asks: list[dict] = []

    def handler(question, options, meta):
        asks.append({"question": question, "options": options, "meta": meta})
        return "1"

    from scripted_ai import ScriptedAI, finish, resp
    from skills import AskUserSkill

    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=".", allow_write=False))
    reg.register(AskUserSkill(ask_handler=handler))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    finish(
                        "推荐方案 A+C，只改一个文件。"
                        "是否同意此计划？确认后我立即执行。"
                    )
                ),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.PLAN,
        max_steps=4,
    )
    result = agent.plan("修两个 P0")
    assert result.completed
    assert not asks
    assert result.plan is None or not result.plan.ok
    assert "推荐方案 A+C" in result.answer
    assert "是否同意此计划" in result.answer


def test_plan_mode_blocks_write_allows_read(tmp_path):
    from scripted_ai import ScriptedAI, resp, submit_plan, tc

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    ai = ScriptedAI(
        [
            resp(tc("local_file", {"action": "read", "path": "a.txt"}, id="r1")),
            resp(tc("local_file", {"action": "write", "path": "b.txt", "content": "x"}, id="w1")),
            resp(
                submit_plan(
                    "计划已就绪",
                    [
                        {
                            "skill": "local_file",
                            "input": {"action": "write", "path": "b.txt", "content": "x"},
                            "why": "写入",
                        }
                    ],
                    thought="给出计划",
                )
            ),
        ]
    )

    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.PLAN,
        max_steps=6,
    )
    result = agent.plan("规划写入")
    assert result.completed
    assert result.plan.ok
    assert result.plan.steps[0].arguments["action"] == "write"
    assert not (tmp_path / "b.txt").exists()
    obs = "\n".join(c.observation or "" for s in result.steps for c in s.calls)
    assert "hello" in obs
    assert "Plan Mode 禁止" in obs


def test_execute_plan(tmp_path):
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = Agent(
        ai=_NoAI(),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
    )
    plan = Plan(
        summary="写文件",
        steps=[
            PlanStep(
                index=1,
                skill="local_file",
                arguments={"action": "write", "path": "out.txt", "content": "planned"},
                why="写入结果",
            )
        ],
    )
    result = agent.execute_plan(plan)
    assert result.completed
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "planned"
    assert "计划执行完成" in result.answer
    assert "out.txt" in result.answer
    assert "[1] local_file" not in result.answer


def test_execute_plan_merges_same_file_summary(tmp_path):
    target = tmp_path / "A.cs"
    target.write_text("void Foo() {}\nvoid Bar() {}\n", encoding="utf-8")
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = Agent(
        ai=_NoAI(),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
    )
    plan = Plan(
        summary="改两处",
        steps=[
            PlanStep(
                index=1,
                skill="local_file",
                arguments={
                    "action": "patch",
                    "path": "A.cs",
                    "old_text": "void Foo() {}",
                    "new_text": "void Foo() { return; }",
                },
            ),
            PlanStep(
                index=2,
                skill="local_file",
                arguments={
                    "action": "patch",
                    "path": "A.cs",
                    "old_text": "void Bar() {}",
                    "new_text": "void Bar() { return; }",
                },
            ),
        ],
    )
    result = agent.execute_plan(plan)
    assert result.completed
    assert "1 个文件" in result.answer
    assert "2 处" in result.answer
    assert result.answer.count("A.cs") == 1
    assert "[1] local_file" not in result.answer
    assert "[2] local_file" not in result.answer
