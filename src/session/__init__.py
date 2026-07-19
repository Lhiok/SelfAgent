"""会话层：Conversation / RunControl / 展示类型。"""

from __future__ import annotations

from typing import Any

from session.mode import AgentMode
from session.control import RunControl
from session.detail import (
    DETAIL_FULL,
    DETAIL_LEVELS,
    DETAIL_OFF,
    DETAIL_SUMMARY,
    format_result_detail,
    format_step_detail,
    parse_detail_level,
)
from session.doom_loop import DoomLoopTracker, DoomVerdict
from session.types import (
    ActionCall,
    AgentResult,
    AgentStep,
    DetailHandler,
    PlanResult,
    ProgressHandler,
)
from session.changes import (
    build_unified_diff,
    collect_changes_from_steps,
    extract_change_from_call,
    merge_changes_by_path,
)

__all__ = [
    "ActionCall",
    "Agent",
    "AgentMode",
    "AgentResult",
    "AgentStep",
    "Conversation",
    "ConversationState",
    "DEFAULT_PERSIST_DIR",
    "DEFAULT_SYSTEM_PROMPT",
    "DETAIL_FULL",
    "DETAIL_LEVELS",
    "DETAIL_OFF",
    "DETAIL_SUMMARY",
    "DetailHandler",
    "DoomLoopTracker",
    "DoomVerdict",
    "Plan",
    "PlanResult",
    "PlanStep",
    "ProgressHandler",
    "RunControl",
    "TurnRecord",
    "build_unified_diff",
    "clean_plan_summary",
    "collect_changes_from_steps",
    "extract_change_from_call",
    "hard_trim",
    "looks_like_choice_prompt",
    "merge_changes_by_path",
    "format_result_detail",
    "format_step_detail",
    "parse_detail_level",
]


def __getattr__(name: str) -> Any:
    if name in {"Conversation", "ConversationState", "TurnRecord", "DEFAULT_PERSIST_DIR"}:
        from session import conversation as _c

        return getattr(_c, name)
    if name in {"Agent", "DEFAULT_SYSTEM_PROMPT"}:
        from agent.facade import Agent, DEFAULT_SYSTEM_PROMPT

        return Agent if name == "Agent" else DEFAULT_SYSTEM_PROMPT
    if name in {"Plan", "PlanStep", "clean_plan_summary", "looks_like_choice_prompt"}:
        from plan import model as _m

        return getattr(_m, name)
    if name == "hard_trim":
        from memory.compact import hard_trim

        return hard_trim
    if name == "compact_messages":
        from memory.compact import compact_transcript

        def compact_messages(messages, *, ai=None, compact_after=32, keep_recent=12):
            return compact_transcript(
                messages,
                session=None,
                ai=ai,
                compact_after=compact_after,
                keep_recent=keep_recent,
                mark_boundary=False,
            )

        return compact_messages
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
