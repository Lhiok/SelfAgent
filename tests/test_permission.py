"""权限流水线 / 模式 / 规则。"""

from __future__ import annotations

import config as cfg
from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard, PermissionMode
from permission.rules import parse_rule, rule_matches
from permission.types import PermissionBehavior
from react import ReActAgent
from skills import LocalFileSkill, SkillRegistry


def test_rule_parse_and_match():
    r = parse_rule("local_file(list)", PermissionBehavior.ALLOW)
    assert r.tool_name == "local_file"
    assert r.content == "list"
    assert rule_matches(r, "local_file", "list")
    assert not rule_matches(r, "local_file", "write")
    wild = parse_rule("shell_run(*)", PermissionBehavior.ASK)
    assert rule_matches(wild, "shell_run", "run")
    prefix = parse_rule("git_ops(stat:*)", PermissionBehavior.ALLOW)
    assert rule_matches(prefix, "git_ops", "status")


def test_readonly_allows_read_denies_write():
    guard = PermissionGuard.from_rules(
        allow=["local_file(list)", "local_file(read)"],
        default_effect="deny",
    )
    assert guard.check("local_file", arguments={"action": "read", "path": "a.txt"}).allowed
    denied = guard.check("local_file", arguments={"action": "write", "path": "a.txt"})
    assert not denied.allowed
    assert denied.suggestions


def test_deny_beats_allow():
    guard = PermissionGuard.from_rules(
        allow=["local_file"],
        deny=["local_file(write)"],
        default_effect="deny",
    )
    assert guard.check("local_file", arguments={"action": "read"}).allowed
    assert not guard.check("local_file", arguments={"action": "write"}).allowed


def test_ask_returns_ask_behavior():
    guard = PermissionGuard.from_rules(
        allow=["local_file(list)", "local_file(read)"],
        ask=["local_file(write)"],
        default_effect="deny",
    )
    d = guard.check("local_file", arguments={"action": "write", "path": "x"})
    assert not d.allowed
    assert d.behavior is PermissionBehavior.ASK
    assert d.suggestions


def test_bypass_mode_allows():
    guard = PermissionGuard.from_rules(
        allow=[],
        default_effect="deny",
        mode=PermissionMode.BYPASS,
    )
    assert guard.check("shell_run", arguments={"action": "run"}).allowed


def test_plan_mode_blocks_write_allows_read():
    guard = PermissionGuard.allow_all()
    guard.bind_context(
        plan_active=True,
        allow_readonly_in_plan=True,
        readonly_actions={"local_file": ["list", "read"]},
        plan_allow_skills=["ask_user"],
    )
    assert guard.check("local_file", arguments={"action": "read"}).allowed
    assert not guard.check("local_file", arguments={"action": "write"}).allowed
    assert guard.check("ask_user").allowed


def test_registry_enforces_permission(tmp_path):
    guard = PermissionGuard.from_rules(
        allow=["local_file(list)", "local_file(read)"],
        default_effect="deny",
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

    guard = PermissionGuard.from_rules(
        allow=["local_file(list)", "local_file(read)"],
        default_effect="deny",
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


def test_legacy_roles_compile():
    prev = cfg.get_config()
    try:
        cfg.set_config(
            {
                "permission": {
                    "enabled": True,
                    "default_effect": "deny",
                    "role": "readonly",
                    "roles": {
                        "readonly": {
                            "skills": {
                                "local_file": {"actions": ["list", "read"]},
                            }
                        }
                    },
                }
            }
        )
        guard = PermissionGuard.from_config()
        assert guard.check("local_file", arguments={"action": "read"}).allowed
        assert not guard.check("local_file", arguments={"action": "write"}).allowed
    finally:
        cfg.set_config(prev)
