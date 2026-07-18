from __future__ import annotations

import config as cfg
from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from permission.guard import RolePolicy, SkillRule
from react import ReActAgent
from skills import LocalFileSkill, SkillRegistry


def test_readonly_allows_read_denies_write():
    guard = PermissionGuard(
        enabled=True,
        role="readonly",
        default_effect="deny",
        roles={
            "readonly": RolePolicy(
                name="readonly",
                skills={"local_file": SkillRule(allow=True, actions={"list", "read"})},
            )
        },
    )
    assert guard.check("local_file", arguments={"action": "read", "path": "a.txt"}).allowed
    denied = guard.check("local_file", arguments={"action": "write", "path": "a.txt"})
    assert not denied.allowed
    assert "write" in denied.reason


def test_registry_enforces_permission(tmp_path):
    guard = PermissionGuard(
        enabled=True,
        role="readonly",
        default_effect="deny",
        roles={
            "readonly": RolePolicy(
                name="readonly",
                skills={"local_file": SkillRule(allow=True, actions={"list", "read"})},
            )
        },
    )
    reg = SkillRegistry(permission=guard)
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")

    ok = reg.run("local_file", {"action": "read", "path": "a.txt"})
    assert ok.ok and "ok" in ok.output

    blocked = reg.run("local_file", {"action": "write", "path": "b.txt", "content": "x"})
    assert not blocked.ok
    assert "权限拒绝" in blocked.output


def test_react_agent_blocks_forbidden_action(tmp_path):
    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                content = """Thought: 尝试写入
Action: local_file
Action Input: {"action":"write","path":"x.txt","content":"nope"}
"""
            else:
                content = "Thought: 结束\nFinal Answer: done"
            return AIResponse(content=content, model="s", provider=self.provider)

    guard = PermissionGuard(
        enabled=True,
        role="readonly",
        default_effect="deny",
        roles={
            "readonly": RolePolicy(
                name="readonly",
                skills={"local_file": SkillRule(allow=True, actions={"list", "read"})},
            )
        },
    )
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = ReActAgent(
        ai=_AI(),
        skills=reg,
        permission=guard,
        max_steps=5,
        system_prompt="test",
    )
    result = agent.run("写文件")
    assert result.completed
    assert not (tmp_path / "x.txt").exists()
    assert any("权限拒绝" in (c.observation or "") for c in result.steps[0].calls)


def test_from_config_missing_section_allows_all():
    prev = cfg.get_config()
    try:
        cfg.set_config({})
        guard = PermissionGuard.from_config()
        assert not guard.enabled
        assert guard.check("local_file", arguments={"action": "write"}).allowed
    finally:
        cfg.set_config(prev)
