"""原生 tool 循环核心契约。"""

from __future__ import annotations

from permission import PermissionGuard
from session import Agent
from scripted_ai import ScriptedAI, finish, resp, tc
from skills import LocalFileSkill, SkillRegistry


def test_finish_without_tools():
    ai = ScriptedAI([resp(finish("hello"))])
    agent = Agent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        max_steps=3,
    )
    result = agent.run("hi")
    assert result.completed
    assert result.answer == "hello"


def test_multi_tool_then_finish(tmp_path):
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    ai = ScriptedAI(
        [
            resp(
                tc("local_file", {"action": "read", "path": "a.txt"}, id="1"),
                tc("local_file", {"action": "read", "path": "b.txt"}, id="2"),
            ),
            resp(finish("ok")),
        ]
    )
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        max_steps=5,
        workdir=tmp_path,
    )
    result = agent.run("读")
    assert result.completed
    assert len(result.steps[0].calls) == 2
    assert any(m.role == "tool" for m in result.messages)


def test_cancel_synthesizes_tool_results(tmp_path):
    from session.control import RunControl

    control = RunControl()

    class _AI(ScriptedAI):
        def chat(self, messages, options=None):
            control.cancel()
            return super().chat(messages, options)

    ai = _AI(
        [
            resp(tc("local_file", {"action": "list", "path": "."}, id="x")),
        ]
    )
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    agent = Agent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        control=control,
        max_steps=3,
        workdir=tmp_path,
    )
    result = agent.run("list")
    assert not result.completed
    assert result.stop_reason == "cancelled"
