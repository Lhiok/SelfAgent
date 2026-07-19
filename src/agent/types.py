"""Agent 循环类型（对齐 Claude Code queryLoop 状态）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ai import AIMessage, ToolCall
from session.mode import AgentMode
from plan.model import Plan
from session.types import ActionCall, AgentStep


class TerminalReason(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    MAX_TURNS = "max_turns"
    DOOM_LOOP = "doom_loop"
    ERROR = "error"
    INCOMPLETE = "incomplete"


@dataclass
class ToolResultMsg:
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)

    def to_ai_message(self) -> AIMessage:
        return AIMessage(
            role="tool",
            content=self.content,
            name=self.name,
            tool_call_id=self.tool_call_id,
        )


@dataclass
class LoopState:
    messages: list[AIMessage]
    mode: AgentMode = AgentMode.AGENT
    turn_count: int = 0
    max_turns: int = 12
    steps: list[AgentStep] = field(default_factory=list)
    answer: str = ""
    plan: Plan | None = None
    stop_reason: str = ""
    completed: bool = False
    pending_ask: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TurnOutcome:
    reason: TerminalReason
    state: LoopState


def tool_call_to_action(tc: ToolCall, result: ToolResultMsg | None = None) -> ActionCall:
    return ActionCall(
        action=tc.name,
        action_input=tc.arguments or "{}",
        observation=None if result is None else result.content,
        ok=None if result is None else result.ok,
        data=dict(result.data) if result is not None else {},
    )
