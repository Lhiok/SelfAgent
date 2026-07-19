"""Hooks 完整事件：command block / PostToolUse / Stop。"""

from __future__ import annotations

import sys
from pathlib import Path

from agent.types import LoopState
from hooks import (
    ALL_HOOK_EVENTS,
    HookDefinition,
    HookEvent,
    HookRegistry,
    emit,
    set_hook_registry,
)
from permission import PermissionGuard
from session.mode import AgentMode


def test_hook_event_table_complete() -> None:
    names = {e.value for e in ALL_HOOK_EVENTS}
    for required in {
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "UserPromptSubmit",
        "SessionStart",
        "SessionEnd",
        "Stop",
        "StopFailure",
        "SubagentStart",
        "SubagentStop",
        "PreCompact",
        "PostCompact",
        "PermissionRequest",
    }:
        assert required in names


def test_command_hook_blocks_local_file_write(tmp_path: Path) -> None:
    block = Path(__file__).resolve().parent / "fixtures" / "hook_block.py"
    reg = HookRegistry(
        [
            HookDefinition(
                event=HookEvent.PRE_TOOL_USE,
                runner="command",
                matcher="local_file",
                command=f'"{sys.executable}" "{block}"',
            )
        ]
    )
    set_hook_registry(reg)
    try:
        guard = PermissionGuard(enabled=True, default_effect="allow")
        # pipeline 步骤 3 经 run_pre_tool_hooks → registry
        d = guard.check(
            "local_file",
            action="write",
            arguments={"action": "write", "path": "x.txt", "content": "a"},
        )
        assert not d.allowed
        assert "hook" in d.reason.lower() or d.reason
    finally:
        set_hook_registry(HookRegistry.empty())


def test_stop_hook_runs_after_finish(tmp_path: Path) -> None:
    marker = tmp_path / "stopped.txt"
    cmd = f'{sys.executable} -c "open(r\'{marker.as_posix()}\',\'w\').write(\'ok\')"'
    reg = HookRegistry(
        [
            HookDefinition(
                event=HookEvent.STOP,
                runner="command",
                matcher="*",
                command=cmd,
            )
        ]
    )
    set_hook_registry(reg)
    try:
        from agent.hooks import run_stop_hooks

        state = LoopState(messages=[], mode=AgentMode.AGENT, answer="done", completed=True)
        run_stop_hooks(None, state, success=True)
        assert marker.is_file()
        assert marker.read_text(encoding="utf-8") == "ok"
    finally:
        set_hook_registry(HookRegistry.empty())


def test_post_tool_use_records(tmp_path: Path) -> None:
    marker = tmp_path / "post.txt"
    cmd = (
        f'{sys.executable} -c "import sys,json; '
        f"d=json.load(sys.stdin); open(r'{marker.as_posix()}','w').write(d.get('skill',''))\""
    )
    reg = HookRegistry(
        [
            HookDefinition(
                event=HookEvent.POST_TOOL_USE,
                runner="command",
                matcher="*",
                command=cmd,
            )
        ]
    )
    set_hook_registry(reg)
    try:
        result = emit(
            HookEvent.POST_TOOL_USE,
            tool_name="local_file",
            payload={"skill": "local_file", "ok": True, "output": "x"},
        )
        assert not result.blocked
        assert marker.is_file()
        assert "local_file" in marker.read_text(encoding="utf-8")
    finally:
        set_hook_registry(HookRegistry.empty())
