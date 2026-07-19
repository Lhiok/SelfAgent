"""Skill 与原生 tool_calls Agent 回归（已移除文本 Thought/Action 解析）。"""

from __future__ import annotations

from permission import PermissionGuard
from session import Agent
from skills import LocalFileSkill, SkillRegistry


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


def test_agent_runs_multiple_actions(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp, tc

    (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")
    registry = SkillRegistry()
    registry.register(LocalFileSkill(root=tmp_path, allow_write=False))

    ai = ScriptedAI(
        [
            resp(
                tc("local_file", {"action": "read", "path": "a.txt"}, id="c1"),
                tc("local_file", {"action": "read", "path": "b.txt"}, id="c2"),
            ),
            resp(finish("都读到了")),
        ]
    )
    agent = Agent(
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
    tool_obs = [m.content for m in result.messages if m.role == "tool"]
    assert any("AAA" in c for c in tool_obs)
    assert any("BBB" in c for c in tool_obs)
