from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import ReActAgent
from skills import LocalFileSkill, SearchCodeSkill, SkillRegistry


def test_registry_set_workdir(tmp_path):
    other = tmp_path / "proj"
    other.mkdir()
    (other / "a.txt").write_text("hi", encoding="utf-8")

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    reg.register(SearchCodeSkill(root=tmp_path))
    reg.set_workdir(other)
    assert reg.workdir == other.resolve()
    assert reg.get("local_file").root == other.resolve()
    assert reg.get("search_code").root == other.resolve()

    listed = reg.run("local_file", {"action": "list", "path": "."})
    assert listed.ok
    assert "a.txt" in listed.output


def test_agent_workdir_in_prompt_and_skills(tmp_path):
    work = tmp_path / "workspace"
    work.mkdir()
    (work / "readme.md").write_text("# demo", encoding="utf-8")

    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0
            self.last_system = ""

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.last_system = messages[0].content
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: 列目录\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"list","path":"."}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content="Thought: ok\nFinal Answer: 看到 readme.md\n",
                model="s",
                provider=self.provider,
            )

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    ai = _AI()
    agent = ReActAgent(
        ai=ai,
        skills=reg,
        permission=PermissionGuard.allow_all(),
        workdir=work,
        detail="off",
        max_steps=5,
    )
    assert agent.workdir == work.resolve()
    assert "当前工作目录" in agent.system_prompt
    assert str(work.resolve()) in agent.system_prompt

    result = agent.run("列出文件")
    assert result.completed
    assert "readme.md" in (result.steps[0].observation or "")
    assert str(work.resolve()) in ai.last_system


def test_set_workdir_runtime(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (b / "only-b.txt").write_text("x", encoding="utf-8")

    class _AI(AIClient):
        provider = "scripted"

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            return AIResponse(
                content="Thought: x\nFinal Answer: ok\n",
                model="s",
                provider=self.provider,
            )

    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=a, allow_write=False))
    agent = ReActAgent(
        ai=_AI(),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        workdir=a,
        detail="off",
    )
    agent.set_workdir(b)
    assert agent.workdir == b.resolve()
    assert str(b.resolve()) in agent.system_prompt
    listed = agent.skills.run("local_file", {"action": "list", "path": "."})
    assert listed.ok
    assert "only-b.txt" in listed.output
