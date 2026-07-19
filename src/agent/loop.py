"""queryLoop：原生 tool_calls 循环（对齐 Claude Code query.ts）。"""

from __future__ import annotations

import json
from typing import Any, Callable

from ai import AIClient, AIMessage, AIResponse, ChatOptions, StreamEvent, ToolCall
from agent.context_pipeline import prepare_messages
from agent.hooks import StopHook, run_stop_hooks
from agent.tool_runner import ToolRunner
from agent.tools import (
    ENTER_PLAN_MODE_TOOL,
    FINISH_TOOL,
    SUBMIT_PLAN_TOOL,
)
from agent.types import LoopState, TerminalReason, TurnOutcome, tool_call_to_action
from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from session.doom_loop import DoomLoopTracker
from session.mode import AgentMode
from session.types import ActionCall, ProgressHandler, AgentStep
from skills.bridge import SkillBridge
from skills.registry import SkillRegistry

logger = get_logger("agent.loop")


def query_loop(
    state: LoopState,
    *,
    ai: AIClient,
    skills: SkillRegistry,
    bridge: SkillBridge,
    permission: PermissionGuard,
    control: RunControl,
    tools_schema: list[dict[str, Any]],
    doom: DoomLoopTracker,
    on_progress: ProgressHandler | None = None,
    on_step: Callable[[AgentStep], None] | None = None,
    on_enter_plan: Callable[[str], None] | None = None,
    stop_hooks: list[StopHook] | None = None,
    max_concurrency: int = 4,
    empty_nudge_max: int = 2,
    step_extend: Callable[[LoopState], bool] | None = None,
    mcp_tools: list[Any] | None = None,
    include_run_subagent: bool = True,
) -> TurnOutcome:
    """同步 query 循环；就地更新 state 并返回终止原因。"""
    runner = ToolRunner(
        bridge,
        permission,
        control=control,
        on_progress=on_progress,
        on_enter_plan=on_enter_plan,
        max_concurrency=max_concurrency,
    )
    empty_nudges = 0
    permission.set_plan_active(state.mode is AgentMode.PLAN)

    while True:
        if control.cancel_requested:
            state.stop_reason = "cancelled"
            state.completed = False
            state.answer = state.answer or "已取消。"
            run_stop_hooks(stop_hooks, state, success=False)
            return TurnOutcome(TerminalReason.CANCELLED, state)

        # 中途插话
        pending = control.drain_pending()
        if pending:
            text = "\n".join(pending)
            state.messages.append(
                AIMessage(role="user", content=f"用户中途补充:\n{text}")
            )
            if on_progress:
                on_progress(
                    {
                        "type": "status",
                        "phase": "interjection",
                        "message": "已注入用户补充",
                    }
                )

        if state.turn_count >= state.max_turns:
            if step_extend is not None and step_extend(state):
                continue
            state.stop_reason = "max_turns"
            state.completed = False
            state.answer = state.answer or f"已达步数上限（{state.max_turns}）。"
            run_stop_hooks(stop_hooks, state, success=False)
            return TurnOutcome(TerminalReason.MAX_TURNS, state)

        prepared = prepare_messages(state.messages)
        if on_progress:
            on_progress(
                {
                    "type": "status",
                    "phase": "thinking",
                    "step": state.turn_count + 1,
                    "message": "思考中…",
                }
            )

        try:
            response = _call_model(
                ai,
                prepared.messages,
                tools_schema,
                control=control,
                on_progress=on_progress,
                step=state.turn_count + 1,
            )
        except Exception as exc:  # noqa: BLE001
            if control.cancel_requested:
                state.stop_reason = "cancelled"
                state.answer = "已取消。"
                run_stop_hooks(stop_hooks, state, success=False)
                return TurnOutcome(TerminalReason.CANCELLED, state)
            logger.critical(f"模型调用失败: {exc}")
            state.stop_reason = "error"
            state.answer = f"模型调用失败: {exc}"
            run_stop_hooks(stop_hooks, state, success=False)
            return TurnOutcome(TerminalReason.ERROR, state)

        from agent.streaming_tools import StreamingToolCollector

        # 统一为 StreamingToolCollector 形状（当前 batch chat；日后可接真流式）
        collected = StreamingToolCollector().from_complete(
            content=response.content or "",
            tool_calls=list(response.tool_calls),
        )

        if control.cancel_requested:
            if collected.tool_calls:
                for tr in runner.synthetic_cancel_results(collected.tool_calls):
                    state.messages.append(tr.to_ai_message())
            state.stop_reason = "cancelled"
            state.answer = "已取消。"
            run_stop_hooks(stop_hooks, state, success=False)
            return TurnOutcome(TerminalReason.CANCELLED, state)

        assistant = AIMessage(
            role="assistant",
            content=collected.content or "",
            tool_calls=list(collected.tool_calls),
        )
        state.messages.append(assistant)

        step = AgentStep(
            index=state.turn_count + 1,
            thought=(collected.content or "").strip(),
            raw_model_output=collected.content or "",
        )

        if not collected.tool_calls:
            # 无 tool：尝试把文本当答案；或空回复 nudge
            text = (collected.content or "").strip()
            if not text:
                if runner.ask_buffer:
                    ask_call, attach = runner.flush_pending_asks()
                    if ask_call is not None:
                        step.calls.append(ask_call)
                    if attach is not None:
                        state.messages.append(attach)
                    state.steps.append(step)
                    if on_step is not None:
                        on_step(step)
                    state.turn_count += 1
                    continue
                empty_nudges += 1
                if empty_nudges <= empty_nudge_max:
                    state.messages.append(
                        AIMessage(
                            role="user",
                            content="请调用工具继续，或调用 finish 给出最终答案。",
                        )
                    )
                    continue
                state.completed = False
                state.stop_reason = "incomplete"
                state.answer = "模型未返回有效内容。"
                state.steps.append(step)
                run_stop_hooks(stop_hooks, state, success=False)
                return TurnOutcome(TerminalReason.INCOMPLETE, state)

            if runner.ask_buffer:
                ask_call, attach = runner.flush_pending_asks()
                if ask_call is not None:
                    step.calls.append(ask_call)
                if attach is not None:
                    state.messages.append(attach)
                state.steps.append(step)
                if on_step is not None:
                    on_step(step)
                state.turn_count += 1
                continue

            step.final_answer = text
            state.steps.append(step)
            if on_step is not None:
                on_step(step)
            state.answer = text
            state.completed = True
            run_stop_hooks(stop_hooks, state, success=True)
            return TurnOutcome(TerminalReason.COMPLETED, state)

        # 有 tool_calls
        if on_progress:
            on_progress(
                {
                    "type": "status",
                    "phase": "acting",
                    "step": step.index,
                    "actions": [tc.name for tc in collected.tool_calls],
                    "message": "执行工具…",
                }
            )

        batch = runner.run_calls(collected.tool_calls)

        # 先写齐 tool results（API 不变量）
        result_by_id = {r.tool_call_id: r for r in batch.tool_results}
        for tc in collected.tool_calls:
            tr = result_by_id.get(tc.id)
            if tr is None:
                # finish/submit 已在 batch；ask buffer 也在
                continue
            state.messages.append(tr.to_ai_message())
            if tc.name not in {
                FINISH_TOOL,
                SUBMIT_PLAN_TOOL,
                ENTER_PLAN_MODE_TOOL,
            } or tr.data.get("buffered"):
                step.calls.append(tool_call_to_action(tc, tr))
            elif tc.name in {
                FINISH_TOOL,
                SUBMIT_PLAN_TOOL,
                ENTER_PLAN_MODE_TOOL,
            }:
                step.calls.append(tool_call_to_action(tc, tr))

        # 补齐未在 result_by_id 中的（不应发生）
        for tr in batch.tool_results:
            if tr.tool_call_id not in {tc.id for tc in collected.tool_calls}:
                state.messages.append(tr.to_ai_message())

        for attach in batch.user_attachments:
            state.messages.append(attach)

        if batch.entered_plan:
            state.mode = AgentMode.PLAN
            permission.set_plan_active(True)
            tools_schema = build_schemas_for_mode(
                skills,
                permission,
                AgentMode.PLAN,
                mcp_tools=mcp_tools,
                include_run_subagent=include_run_subagent,
            )
            from plan.prompts import plan_mode_prompt

            state.messages.append(
                AIMessage(
                    role="user",
                    content=(
                        "[system] 已切换至 Plan Mode。\n"
                        + plan_mode_prompt()
                        + (
                            f"\n进入原因: {batch.enter_plan_reason}"
                            if batch.enter_plan_reason
                            else ""
                        )
                    ),
                )
            )
            if on_progress:
                on_progress(
                    {
                        "type": "status",
                        "phase": "plan_mode",
                        "message": "已进入 Plan Mode",
                    }
                )
            state.steps.append(step)
            if on_step is not None:
                on_step(step)
            state.turn_count += 1
            continue

        if batch.plan is not None:
            state.plan = batch.plan
            step.final_answer = batch.plan.summary or batch.plan.format_text()
            state.answer = step.final_answer
            state.steps.append(step)
            if on_step is not None:
                on_step(step)
            state.completed = True
            run_stop_hooks(stop_hooks, state, success=True)
            return TurnOutcome(TerminalReason.COMPLETED, state)

        if batch.finish_answer is not None:
            step.final_answer = batch.finish_answer
            state.answer = batch.finish_answer
            state.steps.append(step)
            if on_step is not None:
                on_step(step)
            state.completed = True
            run_stop_hooks(stop_hooks, state, success=True)
            return TurnOutcome(TerminalReason.COMPLETED, state)

        state.steps.append(step)
        if on_step is not None:
            on_step(step)
        state.turn_count += 1

        verdict = doom.observe(step)
        if verdict.triggered:
            if verdict.inject:
                state.messages.append(AIMessage(role="user", content=verdict.inject))
            else:
                state.stop_reason = "doom_loop"
                state.completed = False
                state.answer = verdict.message or "检测到重复循环，已停止。"
                run_stop_hooks(stop_hooks, state, success=False)
                return TurnOutcome(TerminalReason.DOOM_LOOP, state)

        # 继续下一轮
        continue


def _call_model(
    ai: AIClient,
    messages: list[AIMessage],
    tools: list[dict[str, Any]],
    *,
    control: RunControl,
    on_progress: ProgressHandler | None,
    step: int,
) -> AIResponse:
    options = ChatOptions(tools=tools, tool_choice="auto")
    # tools 回合走 chat（DeepSeek stream 在有 tools 时也会降级）
    return ai.chat(messages, options)


def build_schemas_for_mode(
    skills: SkillRegistry,
    permission: PermissionGuard,
    mode: AgentMode,
    *,
    mcp_tools: list[Any] | None = None,
    include_run_subagent: bool = True,
) -> list[dict[str, Any]]:
    from mcp.pool import assemble_tool_pool
    from plan.tools import should_include_enter_plan, should_include_submit_plan

    return assemble_tool_pool(
        skills,
        mcp_tools,
        permission=permission,
        include_submit_plan=should_include_submit_plan(mode),
        include_enter_plan=should_include_enter_plan(mode),
        include_run_subagent=include_run_subagent,
    )
