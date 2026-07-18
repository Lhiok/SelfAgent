from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react.agent import ReActAgent
from skills import SkillRegistry, TodoTrackerSkill


class ScriptedAI(AIClient):
    provider = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.i = 0

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        text = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return AIResponse(content=text, model="s", provider=self.provider)


def test_empty_reply_is_nudged_then_continues(tmp_path):
    ai = ScriptedAI(
        [
            "",
            "Thought: 继续\nAction: todo_tracker\nAction Input: {\"action\":\"list\"}\n",
            "Thought: 完成\nFinal Answer: 已列出清单\n",
        ]
    )
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(TodoTrackerSkill(root=tmp_path))
    agent = ReActAgent(
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
    assert ai.i >= 3


def test_empty_final_answer_is_nudged(tmp_path):
    ai = ScriptedAI(
        [
            "Thought: x\nFinal Answer:\n",
            "Thought: ok\nFinal Answer: 真正结束\n",
        ]
    )
    agent = ReActAgent(
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
