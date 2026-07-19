from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import ReActAgent
from react.parser import parse_react_output
from skills import LocalFileSkill, SkillRegistry


class _ScriptedAI(AIClient):
    provider = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)

    def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
        content = self._replies.pop(0) if self._replies else "Final Answer: done"
        return AIResponse(content=content, model="scripted", provider=self.provider)


def test_local_file_list_read_write_patch(tmp_path):
    skill = LocalFileSkill(root=tmp_path, allow_write=True)
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")

    listed = skill.run(action="list", path=".")
    assert listed.ok
    assert "a.txt" in listed.output

    read = skill.run(action="read", path="a.txt")
    assert read.ok and read.output.startswith("hello")

    patched = skill.run(
        action="patch",
        path="a.txt",
        old_text="world",
        new_text="selfagent",
    )
    assert patched.ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello selfagent"

    # CRLF 文件 + LF old_text 仍应匹配
    (tmp_path / "win.txt").write_bytes(b"line1\r\nline2\r\n")
    crlf_ok = skill.run(
        action="patch",
        path="win.txt",
        old_text="line2\n",
        new_text="patched\n",
    )
    assert crlf_ok.ok, crlf_ok.output
    assert b"patched" in (tmp_path / "win.txt").read_bytes()

    written = skill.run(action="write", path="b.txt", content="new")
    assert written.ok
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "new"

    moved = skill.run(action="move", path="b.txt", dest="subdir/c.txt")
    assert moved.ok
    assert not (tmp_path / "b.txt").exists()
    assert (tmp_path / "subdir" / "c.txt").read_text(encoding="utf-8") == "new"


def test_registry_run_json():
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=".", allow_write=False))
    result = reg.run("local_file", '{"action":"list","path":"."}')
    assert result.ok


def test_parse_react_action():
    text = """Thought: 先看目录
Action: local_file
Action Input: {"action":"list","path":"."}
"""
    parsed = parse_react_output(text)
    assert parsed.action == "local_file"
    assert '"list"' in (parsed.action_input or "")
    assert len(parsed.actions) == 1


def test_parse_react_multiple_actions():
    text = """Thought: 同时读两个文件
Action: local_file
Action Input: {"action":"read","path":"a.txt"}
Action: local_file
Action Input: {"action":"read","path":"b.txt"}
"""
    parsed = parse_react_output(text)
    assert len(parsed.actions) == 2
    assert parsed.actions[0].action == "local_file"
    assert "a.txt" in parsed.actions[0].action_input
    assert "b.txt" in parsed.actions[1].action_input
    # 兼容属性仍指向第一个
    assert parsed.action == "local_file"


def test_parse_react_final():
    text = """Thought: 完成了
Final Answer: 这是答案
"""
    parsed = parse_react_output(text)
    assert parsed.final_answer == "这是答案"
    assert parsed.action is None
    assert parsed.actions == []


def test_parse_react_strips_leaked_action_from_thought():
    text = """Thought: 先确认方案
Action Input: {"questions":[{"id":"1","question":"选哪个？"}]}
Action: ask_user
Action Input: {"questions":[{"id":"1","question":"选哪个？","options":["A","B"]}]}
"""
    parsed = parse_react_output(text)
    assert parsed.action == "ask_user"
    assert "Action Input" not in parsed.thought
    assert "questions" not in parsed.thought
    assert "先确认方案" in parsed.thought


def test_react_agent_runs_multiple_actions(tmp_path):
    (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")
    registry = SkillRegistry()
    registry.register(LocalFileSkill(root=tmp_path, allow_write=False))

    ai = _ScriptedAI(
        [
            """Thought: 一次读两个
Action: local_file
Action Input: {"action":"read","path":"a.txt"}
Action: local_file
Action Input: {"action":"read","path":"b.txt"}
""",
            """Thought: 完成
Final Answer: 都读到了
""",
        ]
    )
    agent = ReActAgent(
        ai=ai,
        skills=registry,
        permission=PermissionGuard.allow_all(),
        max_steps=5,
        system_prompt="test",
    )
    result = agent.run("读 a 和 b")
    assert result.completed
    assert result.answer == "都读到了"
    assert len(result.steps[0].calls) == 2
    assert "AAA" in (result.steps[0].calls[0].observation or "")
    assert "BBB" in (result.steps[0].calls[1].observation or "")
    # 回传给模型的应是多 Observation 汇总
    user_obs = [m.content for m in result.messages if m.role == "user"]
    assert any("Observations:" in c and "AAA" in c and "BBB" in c for c in user_obs)
