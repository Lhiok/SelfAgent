"""Plan 工具策略：enter_plan_mode（Agent）/ submit_plan（Plan Exit）。"""

from __future__ import annotations

from session.mode import AgentMode


def should_include_submit_plan(mode: AgentMode | str) -> bool:
    m = mode if isinstance(mode, AgentMode) else AgentMode.parse(mode)
    return m is AgentMode.PLAN


def should_include_enter_plan(mode: AgentMode | str) -> bool:
    m = mode if isinstance(mode, AgentMode) else AgentMode.parse(mode)
    return m is AgentMode.AGENT


SUBMIT_PLAN_DESCRIPTION = (
    "提交可执行计划并进入用户确认门禁（Plan Mode Exit）。"
    "调用后不会执行写入；用户需 /confirm 或 API confirm 才会执行。"
)

ENTER_PLAN_MODE_DESCRIPTION = (
    "进入 Plan Mode 做只读调研与规划；完成后调用 submit_plan。"
)
