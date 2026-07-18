from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import AgentMode, Plan, PlanStep, ReActAgent, parse_plan
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
