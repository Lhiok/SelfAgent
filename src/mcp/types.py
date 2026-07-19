"""MCP 配置与工具描述类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    transport: str = "stdio"  # stdio | sse
    url: str = ""
    enabled: bool = True


@dataclass
class McpToolDesc:
    server: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        """对外工具名：mcp__{server}__{tool}。"""
        return f"mcp__{self.server}__{self.name}"

    def to_openai_schema(self) -> dict[str, Any]:
        params = self.input_schema or {"type": "object", "properties": {}}
        if "type" not in params:
            params = {"type": "object", "properties": params}
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description or f"MCP {self.server}/{self.name}",
                "parameters": params,
            },
        }


def parse_mcp_tool_name(tool_name: str) -> tuple[str, str] | None:
    """解析 mcp__server__tool → (server, tool)；非法则 None。"""
    raw = (tool_name or "").strip()
    if not raw.startswith("mcp__"):
        return None
    rest = raw[5:]
    if "__" not in rest:
        return None
    server, tool = rest.split("__", 1)
    if not server or not tool:
        return None
    return server, tool
