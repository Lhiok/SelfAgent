"""子 Agent + sidechain JSONL + resume。"""

from __future__ import annotations

from pathlib import Path

from agent.facade import Agent
from permission import PermissionGuard
from scripted_ai import ScriptedAI, finish, resp, tc
from skills.registry import SkillRegistry
from subagent.spawn import run_subagent
from subagent.transcript import load_messages, load_meta, transcript_path


def test_run_subagent_sidechain_and_resume(tmp_path: Path) -> None:
    skills = SkillRegistry.from_config(
        permission=PermissionGuard.allow_all(),
        load_permission=False,
    )
    perm = PermissionGuard.allow_all()
    ai_child = ScriptedAI([resp(finish("child-done"))])
    out = run_subagent(
        prompt="do child work",
        ai=ai_child,
        skills=skills,
        permission=perm,
        description="unit-test",
        max_steps=4,
        session_root=tmp_path,
        agent_id="abc123",
    )
    assert out["ok"]
    assert out["agent_id"] == "abc123"
    assert "child-done" in out["summary"]
    assert transcript_path(tmp_path, "abc123").is_file()
    meta = load_meta(tmp_path, "abc123")
    assert meta is not None
    assert meta.description == "unit-test"
    msgs = load_messages(tmp_path, "abc123")
    assert any(m.role == "user" for m in msgs)
    assert any(m.role == "assistant" and "child-done" in (m.content or "") for m in msgs)

    # resume：同 agent_id 再跑一轮，追加
    ai2 = ScriptedAI([resp(finish("resumed"))])
    out2 = run_subagent(
        prompt="continue",
        ai=ai2,
        skills=skills,
        permission=perm,
        max_steps=4,
        session_root=tmp_path,
        agent_id="abc123",
    )
    assert out2["ok"]
    msgs2 = load_messages(tmp_path, "abc123")
    assert len(msgs2) > len(msgs)
    assert any("resumed" in (m.content or "") for m in msgs2)


def test_parent_calls_run_subagent(tmp_path: Path) -> None:
    skills = SkillRegistry.from_config(
        permission=PermissionGuard.allow_all(),
        load_permission=False,
    )
    # 同一 ScriptedAI：父调 run_subagent → 子 finish → 父 finish
    ai = ScriptedAI(
        [
            resp(tc("run_subagent", {"prompt": "hi", "description": "t", "max_steps": 3})),
            resp(finish("from-child")),
            resp(finish("parent-done")),
        ]
    )
    agent = Agent(
        ai=ai,
        skills=skills,
        permission=PermissionGuard.allow_all(),
        max_steps=8,
        workdir=tmp_path,
    )
    agent.set_session_id("parent-sess")
    result = agent.run("spawn child")
    assert result.completed
    assert "parent-done" in result.answer
    side = Path("logs") / "sessions" / "parent-sess" / "subagents"
    assert side.is_dir()
    assert list(side.glob("agent-*.jsonl"))