from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react.agent import ReActAgent
from skills import AskUserSkill, SkillRegistry, TodoTrackerSkill


class ScriptedAI(AIClient):
    provider = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.i = 0

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        text = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return AIResponse(content=text, model="s", provider=self.provider)


def _agent(tmp_path, ai, ask_raw: str, *, max_steps: int = 2) -> ReActAgent:
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(TodoTrackerSkill(root=tmp_path))
    reg.register(AskUserSkill(ask_handler=lambda q, o, m: ask_raw))
    agent = ReActAgent(
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
    # 2 步用尽 → 用户加 6 步 → 第 3 步给出 Final Answer
    ai = ScriptedAI(
        [
            "Thought: 1\nAction: todo_tracker\nAction Input: {\"action\":\"list\"}\n",
            "Thought: 2\nAction: todo_tracker\nAction Input: {\"action\":\"list\"}\n",
            "Thought: done\nFinal Answer: 加步后续完\n",
        ]
    )
    agent = _agent(tmp_path, ai, ask_raw="1")  # 选增加 6 步
    result = agent.run("长任务")
    assert result.completed
    assert "加步后续完" in (result.answer or "")
    assert ai.i >= 3
    assert any(
        c.action == "ask_user"
        for s in result.steps
        for c in s.calls
    )


def test_max_steps_user_declines(tmp_path):
    ai = ScriptedAI(
        [
            "Thought: 1\nAction: todo_tracker\nAction Input: {\"action\":\"list\"}\n",
            "Thought: 2\nAction: todo_tracker\nAction Input: {\"action\":\"list\"}\n",
            "Thought: should not run\nFinal Answer: 不应到达\n",
        ]
    )
    agent = _agent(tmp_path, ai, ask_raw="3")  # 结束本轮
    result = agent.run("长任务")
    assert not result.completed
    assert "结束" in (result.answer or "") or "上限" in (result.answer or "")
    assert "不应到达" not in (result.answer or "")
    assert ai.i == 2
