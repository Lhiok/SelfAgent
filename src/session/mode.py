"""Agent 运行模式。"""

from __future__ import annotations

from enum import Enum


class AgentMode(str, Enum):
    """agent=直接执行工具；plan=只规划（默认可只读探测）。"""

    AGENT = "agent"
    PLAN = "plan"

    @classmethod
    def parse(cls, value: str | "AgentMode" | None, default: "AgentMode" = AGENT) -> "AgentMode":
        if value is None:
            return default
        if isinstance(value, AgentMode):
            return value
        key = str(value).strip().lower()
        for item in cls:
            if item.value == key:
                return item
        raise ValueError(f"未知运行模式: {value}，可选: {[m.value for m in cls]}")
