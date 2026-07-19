"""MCP tool pool：stdio echo → schema → invoke。"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import McpManager, McpServerConfig, assemble_tool_pool
from permission import PermissionGuard
from skills.registry import SkillRegistry


def test_mcp_echo_list_and_call() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = McpServerConfig(
        name="echo",
        command=sys.executable,
        args=[str(root / "tests" / "fixtures" / "mcp_echo_server.py")],
    )
    mgr = McpManager()
    try:
        tools = mgr.add_server(cfg)
        assert any(t.name == "echo" for t in tools)
        assert "mcp__echo__echo" in {t.tool_name for t in tools}
        ok, text, _ = mgr.call("mcp__echo__echo", {"text": "hi"})
        assert ok
        assert "echo:hi" in text
    finally:
        mgr.close()


def test_assemble_tool_pool_includes_mcp() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = McpServerConfig(
        name="echo",
        command=sys.executable,
        args=[str(root / "tests" / "fixtures" / "mcp_echo_server.py")],
    )
    mgr = McpManager()
    try:
        mgr.add_server(cfg)
        skills = SkillRegistry.from_config(
            permission=PermissionGuard.allow_all(),
            load_permission=False,
        )
        schemas = assemble_tool_pool(
            skills,
            mgr.list_tools(),
            permission=PermissionGuard.allow_all(),
            include_run_subagent=True,
        )
        names = [s["function"]["name"] for s in schemas]
        assert "finish" in names
        assert "run_subagent" in names
        assert "mcp__echo__echo" in names
        # skill 在 MCP 前
        assert names.index("finish") < names.index("mcp__echo__echo")
    finally:
        mgr.close()
