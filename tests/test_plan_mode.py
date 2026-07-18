from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import AgentMode, Plan, PlanStep, ReActAgent, parse_plan
from react.plan import clean_plan_summary, looks_like_choice_prompt
from skills import LocalFileSkill, SkillRegistry


class _NoAI(AIClient):
    provider = "none"

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        raise RuntimeError("不应调用模型")


def test_parse_plan_steps():
    text = """Thought: 先读后改
Plan:
1. skill=local_file | input={"action":"read","path":"a.py"} | why=了解现状
2. skill=local_file | input={"action":"patch","path":"a.py","old_text":"x","new_text":"y"} | why=替换
Final Answer: 两步完成修改
"""
    plan = parse_plan(text, thought="先读后改", final_answer="两步完成修改")
    assert plan.ok
    assert len(plan.steps) == 2
    assert plan.steps[0].arguments["action"] == "read"
    assert plan.steps[1].arguments["action"] == "patch"
    assert "了解现状" in plan.steps[0].why


def test_parse_plan_cleans_summary_and_loose_steps():
    dumped = (
        "用户确认全部采用 A 方案，与之前给出的计划一致。直接执行： Plan: "
        '1. skill=local_file | input={"action":"patch","path":"a.cs","old_text":"x","new_text":"y"} | why="修A" '
        '2. skill=local_file | input={"action":"patch","path":"b.cs","old_text":"u","new_text":"v"} | why="修B"'
    )
    plan = parse_plan(
        "Thought: x\nPlan:\n"
        '1. skill=local_file | input={"action":"patch","path":"a.cs","old_text":"x","new_text":"y"} | why=修A\n'
        '2. skill=local_file | input={"action":"patch","path":"b.cs","old_text":"u","new_text":"v"} | why=修B\n'
        f"Final Answer: {dumped}\n",
        final_answer=dumped,
    )
    assert plan.ok
    assert len(plan.steps) == 2
    assert "skill=" not in plan.summary
    assert "Plan:" not in plan.summary
    assert "用户确认全部采用 A 方案" in plan.summary

    loose = parse_plan(
        dumped,
        final_answer=dumped,
    )
    assert loose.ok
    assert len(loose.steps) == 2
    assert loose.steps[0].arguments["path"] == "a.cs"


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
    """无 skill= Plan 的 Final Answer（含是否同意话术）直接作为回答展示，不弹 ask_user。"""
    asks: list[dict] = []

    def handler(question, options, meta):
        asks.append({"question": question, "options": options, "meta": meta})
        return "1"

    class _AI(AIClient):
        provider = "scripted"

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            return AIResponse(
                content=(
                    "Thought: 直接给方案\n"
                    "Final Answer: 推荐方案 A+C，只改一个文件。"
                    "是否同意此计划？确认后我立即执行。\n"
                ),
                model="s",
                provider=self.provider,
            )

    from skills import AskUserSkill

    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=".", allow_write=False))
    reg.register(AskUserSkill(ask_handler=handler))
    agent = ReActAgent(
        ai=_AI(),
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
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: 先读\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"read","path":"a.txt"}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            if self.n == 2:
                return AIResponse(
                    content=(
                        "Thought: 尝试写入应被拦\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"write","path":"b.txt","content":"x"}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content=(
                    "Thought: 给出计划\n"
                    "Plan:\n"
                    '1. skill=local_file | input={"action":"write","path":"b.txt","content":"x"} | why=写入\n'
                    "Final Answer: 计划已就绪\n"
                ),
                model="s",
                provider=self.provider,
            )

    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = ReActAgent(
        ai=_AI(),
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
    agent = ReActAgent(
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
    agent = ReActAgent(
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
