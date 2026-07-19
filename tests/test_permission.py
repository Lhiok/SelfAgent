"""权限流水线 / 模式 / 规则。"""

from __future__ import annotations

import config as cfg
from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard, PermissionMode
from permission.rules import parse_rule, rule_matches
from permission.types import PermissionBehavior
from session import Agent
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


def test_ask_rule_blocks_with_suggestions():
    guard = PermissionGuard.from_rules(
        allow=["shell_run(run)"],
        ask=["shell_run(*)"],
        default_effect="deny",
    )
    d = guard.check("shell_run", arguments={"action": "run", "command": "echo"})
    assert not d.allowed
    assert d.behavior.value == "ask"
    assert d.suggestions


def test_dont_ask_converts_ask_to_deny():
    guard = PermissionGuard.from_rules(
        ask=["local_file(write)"],
        mode=PermissionMode.DONT_ASK,
        default_effect="deny",
    )
    d = guard.check("local_file", arguments={"action": "write"})
    assert not d.allowed
    assert d.behavior.value == "deny"


def test_bypass_allows_unless_deny():
    guard = PermissionGuard.from_rules(
        deny=["shell_run"],
        mode=PermissionMode.BYPASS,
        default_effect="deny",
    )
    assert guard.check("local_file", arguments={"action": "write"}).allowed
    assert not guard.check("shell_run", arguments={"action": "run"}).allowed


def test_accept_edits_allows_local_file_write(tmp_path):
    guard = PermissionGuard.from_rules(
        mode=PermissionMode.ACCEPT_EDITS,
        default_effect="deny",
    )
    guard.bind_context(workdir=tmp_path)
    assert guard.check(
        "local_file",
        arguments={"action": "write", "path": "a.txt", "content": "x"},
    ).allowed
    assert not guard.check("shell_run", arguments={"action": "run"}).allowed


def test_grant_session_allows_once():
    guard = PermissionGuard.from_rules(default_effect="deny")
    assert not guard.check("local_file", arguments={"action": "write"}).allowed
    guard.grant_session("local_file(write)")
    assert guard.check("local_file", arguments={"action": "write"}).allowed


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


def test_agent_blocks_forbidden_action(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp, tc

    ai = ScriptedAI(
        [
            resp(
                tc(
                    "local_file",
                    {"action": "write", "path": "x.txt", "content": "nope"},
                    id="w1",
                )
            ),
            resp(finish("done")),
        ]
    )
    guard = PermissionGuard.from_rules(
        allow=["local_file(list)", "local_file(read)"],
        default_effect="deny",
    )
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = Agent(
        ai=ai,
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
