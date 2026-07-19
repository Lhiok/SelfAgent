"""LoopState → AgentResult。"""

from __future__ import annotations

from agent.types import LoopState
from session.detail import DETAIL_OFF, format_result_detail
from session.types import AgentResult


def state_to_agent_result(
    state: LoopState,
    *,
    detail_level: str = DETAIL_OFF,
    detail_max_chars: int = 2000,
    stream_detail: bool = False,
) -> AgentResult:
    result = AgentResult(
        answer=state.answer,
        steps=list(state.steps),
        completed=state.completed,
        messages=list(state.messages),
        mode=state.mode.value,
        plan=state.plan,
        detail_level=detail_level,
        stop_reason=state.stop_reason,
    )
    if detail_level != DETAIL_OFF:
        result.detail_text = format_result_detail(
            result, detail_level, max_chars=detail_max_chars
        )
    return result
