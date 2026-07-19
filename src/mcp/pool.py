"""组装内置 skill + MCP 工具 schema（skill 在前，利于 prompt cache）。"""

from __future__ import annotations

from typing import Any

from agent.tools import build_tool_schemas
from mcp.types import McpToolDesc, parse_mcp_tool_name
from permission import PermissionGuard
from skills.registry import SkillRegistry


def assemble_tool_pool(
    skills: SkillRegistry,
    mcp_tools: list[McpToolDesc] | None = None,
    *,
    permission: PermissionGuard | None = None,
    include_submit_plan: bool = False,
    include_enter_plan: bool = False,
    include_run_subagent: bool = True,
) -> list[dict[str, Any]]:
    """内置 skill schema 在前，MCP 工具在后；经 deny 规则过滤。"""
    schemas = build_tool_schemas(
        skills,
        permission,
        include_submit_plan=include_submit_plan,
        include_enter_plan=include_enter_plan,
    )
    if include_run_subagent:
        schemas.append(_run_subagent_schema())

    for tool in filter_mcp_tools(mcp_tools or [], permission):
        schemas.append(tool.to_openai_schema())
    return schemas


def filter_mcp_tools(
    tools: list[McpToolDesc],
    permission: PermissionGuard | None,
) -> list[McpToolDesc]:
    if permission is None:
        return list(tools)
    out: list[McpToolDesc] = []
    for tool in tools:
        decision = permission.check(tool.tool_name, arguments={})
        reason = decision.reason or ""
        if not decision.allowed and "命中 deny" in reason:
            continue
        out.append(tool)
    return out


def is_mcp_tool(name: str) -> bool:
    return parse_mcp_tool_name(name) is not None


def _run_subagent_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "run_subagent",
            "description": (
                "启动子 Agent 执行子任务；结果摘要返回父会话。"
                "适用于可隔离的调研/实现子任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "子任务说明"},
                    "description": {
                        "type": "string",
                        "description": "短描述（写入 sidechain meta）",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "子 Agent 最大步数",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "可选：续跑已有 sidechain agent_id",
                    },
                },
                "required": ["prompt"],
            },
        },
    }
