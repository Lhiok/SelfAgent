"""ReAct 主循环与单步执行。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ai import AIMessage
from log import get_logger
from react.mode import AgentMode
from react.parser import parse_react_output
from react.plan import clean_plan_summary, parse_plan
from react.types import ActionCall, ReActResult, ReActStep, format_observations
from skills.base import SkillResult

if TYPE_CHECKING:
    from react.agent import ReActAgent

logger = get_logger("react")


class LoopMixin:
    """混入 ReActAgent：Thought → Action → Observation 循环。"""

    def _run_loop(
        self: ReActAgent,
        task: str,
        *,
        history: list[AIMessage] | None,
        mode: AgentMode,
        system_prompt: str,
    ) -> ReActResult:
        messages: list[AIMessage] = [
            AIMessage(role="system", content=system_prompt),
        ]
        if history:
            messages.extend(history)
        messages.append(AIMessage(role="user", content=task))
        return self._run_loop_from_messages(messages, mode=mode)

    def _run_loop_from_messages(
        self: ReActAgent,
        messages: list[AIMessage],
        *,
        mode: AgentMode,
    ) -> ReActResult:
        steps: list[ReActStep] = []
        self._ask_buffer.clear()
        self._ask_seq = 0
        self._empty_nudge_count = 0
        if hasattr(self, "_doom_tracker") and self._doom_tracker is not None:
            self._doom_tracker.reset()
        if hasattr(self, "control") and self.control is not None:
            self.control.begin_run()
        self._live_messages = messages
        logger.notice(f"ReAct 开始任务，mode={mode.value}，max_steps={self.max_steps}")

        try:
            return self._run_loop_body(messages, mode=mode, steps=steps)
        finally:
            self._live_messages = None
            if hasattr(self, "control") and self.control is not None:
                self.control.end_run()

    def _run_loop_body(
        self: ReActAgent,
        messages: list[AIMessage],
        *,
        mode: AgentMode,
        steps: list[ReActStep],
    ) -> ReActResult:
        step_budget = max(1, int(self.max_steps))
        hard_cap = max(step_budget, int(self.max_steps_hard_cap))
        i = 0
        while True:
            while i < step_budget:
                i += 1
                if hasattr(self, "control") and self.control is not None:
                    if self.control.cancel_requested:
                        self._emit_progress(
                            {
                                "type": "cancelled",
                                "step": i,
                                "message": "用户取消了本轮任务",
                            }
                        )
                        return self._finalize_result(
                            ReActResult(
                                answer="已取消本轮任务。",
                                steps=steps,
                                completed=False,
                                messages=messages,
                                mode=mode.value,
                                stop_reason="cancelled",
                            )
                        )
                    pending = self.control.drain_pending()
                    if pending:
                        joined = "\n".join(pending)
                        messages.append(
                            AIMessage(
                                role="user",
                                content=(
                                    "Observation:\n"
                                    f"用户中途补充：\n{joined}\n\n"
                                    "请结合上述补充继续执行任务。"
                                ),
                            )
                        )
                        self._emit_progress(
                            {
                                "type": "status",
                                "phase": "interjection",
                                "step": i,
                                "message": f"已注入 {len(pending)} 条中途补充",
                            }
                        )

                result = self._run_one_react_step(
                    messages, mode=mode, steps=steps, step_index=i
                )
                if result is not None:
                    return result

            logger.warning(
                f"ReAct 达到步数上限仍未结束（已用 {i}/{step_budget}，硬上限 {hard_cap}）"
            )
            if self._ask_buffer:
                flush = self._flush_ask_buffer()
                ask_step = ReActStep(
                    index=len(steps) + 1,
                    thought="达到步数上限，先请用户确认暂存问题",
                    calls=[
                        ActionCall(
                            action="ask_user",
                            action_input='{"commit":true}',
                            observation=flush.output
                            if flush.ok
                            else f"ERROR: {flush.output}",
                            ok=flush.ok,
                            data=dict(flush.data or {}),
                        )
                    ],
                )
                steps.append(ask_step)
                self._emit_step(ask_step)
                messages.append(
                    AIMessage(
                        role="user",
                        content=(
                            "Observation:\n"
                            f"{flush.output if flush.ok else 'ERROR: ' + flush.output}\n\n"
                            "以上为用户对暂存问题的确认。系统接下来会询问是否增加本轮可用步数。"
                        ),
                    )
                )

            if hard_cap - step_budget <= 0:
                return self._finalize_result(
                    ReActResult(
                        answer=(
                            f"已达本轮步数硬上限（{hard_cap}），仍未得到 Final Answer。"
                            "请开新对话或提高 config 中 react.max_steps_hard_cap 后再继续。"
                        ),
                        steps=steps,
                        completed=False,
                        messages=messages,
                        mode=mode.value,
                    )
                )

            extra = self._prompt_extend_steps(
                steps=steps,
                messages=messages,
                used=i,
                budget=step_budget,
                hard_cap=hard_cap,
            )
            if extra is None or extra <= 0:
                return self._finalize_result(
                    ReActResult(
                        answer=(
                            f"已达本轮步数上限（{step_budget}），用户选择结束。"
                            "仍未得到 Final Answer；可发送「继续」并同意加步后重试。"
                        ),
                        steps=steps,
                        completed=False,
                        messages=messages,
                        mode=mode.value,
                    )
                )

            new_budget = min(step_budget + extra, hard_cap)
            if new_budget <= step_budget:
                return self._finalize_result(
                    ReActResult(
                        answer=(
                            f"已达本轮步数硬上限（{hard_cap}），无法再增加。"
                            "仍未得到 Final Answer。"
                        ),
                        steps=steps,
                        completed=False,
                        messages=messages,
                        mode=mode.value,
                    )
                )

            logger.notice(f"用户同意延长本轮步数: {step_budget} -> {new_budget}（+{extra}）")
            step_budget = new_budget
            self._emit_progress(
                {
                    "type": "status",
                    "phase": "step_extend",
                    "step": i,
                    "message": f"步数已延长至 {step_budget}，继续执行…",
                }
            )
            messages.append(
                AIMessage(
                    role="user",
                    content=(
                        "Observation:\n"
                        f"用户同意将本轮可用步数延长至 {step_budget}（已用 {i}）。"
                        "请从下一步继续完成任务，不要重复已完成的工作；"
                        "完成后输出非空 Final Answer。"
                    ),
                )
            )

    def _chat_model(self: ReActAgent, messages: list[AIMessage], step_index: int) -> str:
        """调用模型；优先流式并推送 delta，失败则降级为一次性 chat。"""
        stream_enabled = bool(getattr(self, "stream_ai", True))
        cancelled = (
            hasattr(self, "control")
            and self.control is not None
            and self.control.cancel_requested
        )
        if cancelled:
            return ""

        if stream_enabled and hasattr(self.ai, "chat_stream"):
            parts: list[str] = []
            was_cancelled = False
            try:
                for event in self.ai.chat_stream(messages):
                    if hasattr(self, "control") and self.control is not None:
                        if self.control.cancel_requested:
                            was_cancelled = True
                            break
                    text = event if isinstance(event, str) else getattr(event, "text", "") or ""
                    if not text:
                        continue
                    parts.append(text)
                    self._emit_progress(
                        {
                            "type": "assistant_delta",
                            "step": step_index,
                            "delta": text,
                            "phase": "thinking",
                        }
                    )
                if was_cancelled or (
                    hasattr(self, "control")
                    and self.control is not None
                    and self.control.cancel_requested
                ):
                    # 取消后绝不回落同步 chat，否则 UI 会卡在「正在停止…」
                    return "".join(parts).strip()
                content = "".join(parts).strip()
                if content:
                    return content
            except Exception as exc:  # noqa: BLE001
                if (
                    hasattr(self, "control")
                    and self.control is not None
                    and self.control.cancel_requested
                ):
                    return "".join(parts).strip()
                logger.warning(f"流式调用失败，降级为非流式: {exc}")

        if (
            hasattr(self, "control")
            and self.control is not None
            and self.control.cancel_requested
        ):
            return ""
        response = self.ai.chat(messages)
        return (response.content or "").strip()

    def _run_one_react_step(
        self: ReActAgent,
        messages: list[AIMessage],
        *,
        mode: AgentMode,
        steps: list[ReActStep],
        step_index: int,
    ) -> ReActResult | None:
        """执行单步；若本轮应结束则返回 ReActResult，否则返回 None 继续。"""
        i = step_index
        self._emit_progress(
            {
                "type": "status",
                "phase": "thinking",
                "step": i,
                "message": f"第 {i} 步：模型思考中…",
            }
        )
        try:
            raw = self._chat_model(messages, i)
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"AI 调用失败（第 {i} 步）: {exc}")
            err_step = ReActStep(
                index=i,
                thought=f"AI 调用失败: {exc}",
                final_answer=None,
                raw_model_output="",
            )
            steps.append(err_step)
            self._emit_step(err_step)
            return self._finalize_result(
                ReActResult(
                    answer=(
                        f"AI 调用失败（第 {i} 步）: {exc}\n"
                        "会话未中断，可调整超时后重试，或换个更短的问题。"
                    ),
                    steps=steps,
                    completed=False,
                    messages=messages,
                    mode=mode.value,
                )
            )
        parsed = parse_react_output(raw)
        has_final = (
            parsed.final_answer is not None
            and bool(str(parsed.final_answer).strip())
        )
        step = ReActStep(
            index=i,
            thought=parsed.thought,
            final_answer=parsed.final_answer if has_final else None,
            raw_model_output=raw,
        )

        messages.append(AIMessage(role="assistant", content=raw or "(空回复)"))

        if has_final:
            if self._ask_buffer:
                messages[-1] = AIMessage(
                    role="assistant",
                    content=(
                        f"Thought: {parsed.thought or '需要用户确认细节'}\n"
                        "Action: ask_user\n"
                        'Action Input: {"commit": true}\n'
                    ),
                )
                flush = self._flush_ask_buffer()
                step.calls.append(
                    ActionCall(
                        action="ask_user",
                        action_input='{"commit":true}',
                        observation=flush.output
                        if flush.ok
                        else f"ERROR: {flush.output}",
                        ok=flush.ok,
                        data=dict(flush.data or {}),
                    )
                )
                steps.append(step)
                self._emit_step(step)
                messages.append(
                    AIMessage(
                        role="user",
                        content=(
                            "Observation:\n"
                            f"{flush.output if flush.ok else 'ERROR: ' + flush.output}\n\n"
                            "用户已确认全部暂存问题。请根据选择输出最终 Plan 与 Final Answer。"
                            "Final Answer 只写简短摘要，禁止再粘贴选项表、请用户回复选择、或 Plan JSON。"
                        ),
                    )
                )
                return None

            plan = None
            answer = parsed.final_answer
            if mode is AgentMode.PLAN:
                plan = parse_plan(
                    raw,
                    thought=parsed.thought,
                    final_answer=parsed.final_answer,
                )
                if plan is not None and plan.ok:
                    answer = clean_plan_summary(
                        plan.summary or answer, has_steps=True
                    )
                    plan.summary = answer
            steps.append(step)
            self._emit_step(step)
            logger.notice(f"ReAct 完成于第 {i} 步（mode={mode.value}）")
            return self._finalize_result(
                ReActResult(
                    answer=answer,
                    steps=steps,
                    completed=True,
                    messages=messages,
                    mode=mode.value,
                    plan=plan,
                )
            )

        if not parsed.actions:
            if self._ask_buffer:
                logger.notice(
                    f"第 {i} 步无标准 Action/Final Answer，但有暂存 ask_user，先请用户确认"
                )
                messages[-1] = AIMessage(
                    role="assistant",
                    content=(
                        f"Thought: {parsed.thought or '问题已收集完毕，请用户确认'}\n"
                        "Action: ask_user\n"
                        'Action Input: {"commit": true}\n'
                    ),
                )
                flush = self._flush_ask_buffer()
                step.calls.append(
                    ActionCall(
                        action="ask_user",
                        action_input='{"commit":true}',
                        observation=flush.output
                        if flush.ok
                        else f"ERROR: {flush.output}",
                        ok=flush.ok,
                        data=dict(flush.data or {}),
                    )
                )
                steps.append(step)
                self._emit_step(step)
                messages.append(
                    AIMessage(
                        role="user",
                        content=(
                            "Observation:\n"
                            f"{flush.output if flush.ok else 'ERROR: ' + flush.output}\n\n"
                            "用户已确认全部暂存问题。请根据选择输出最终 Plan 与 Final Answer。"
                            "Final Answer 只写简短摘要，禁止再粘贴选项表。"
                        ),
                    )
                )
                return None

            if self._empty_nudge_count < 2:
                self._empty_nudge_count += 1
                reason = (
                    "空回复"
                    if not raw
                    else (
                        "空 Final Answer"
                        if parsed.final_answer is not None
                        else "未解析到 Action/Final Answer"
                    )
                )
                logger.warning(
                    f"第 {i} 步{reason}，续跑提醒（{self._empty_nudge_count}/2）"
                )
                steps.append(step)
                self._emit_step(step)
                messages.append(
                    AIMessage(
                        role="user",
                        content=(
                            "Observation:\n"
                            f"系统：上一步无效（{reason}）。任务若未完成，请继续输出 "
                            "Thought + Action/Action Input；"
                            "仅在全部完成后输出非空 Final Answer。\n"
                            "若使用 todo_tracker：先 start 当前项 → 执行工具 → 再 complete。"
                        ),
                    )
                )
                return None

            logger.warning(f"第 {i} 步未解析到 Action/Final Answer，结束循环")
            fallback = (parsed.thought or raw or "").strip() or (
                "模型连续空回复，任务未完成。请发送「继续」重试。"
            )
            step.final_answer = fallback
            plan = None
            if mode is AgentMode.PLAN:
                plan = parse_plan(raw, thought=parsed.thought, final_answer=fallback)
            steps.append(step)
            self._emit_step(step)
            return self._finalize_result(
                ReActResult(
                    answer=fallback,
                    steps=steps,
                    completed=False,
                    messages=messages,
                    mode=mode.value,
                    plan=plan,
                )
            )

        action_names = [item.action for item in parsed.actions]
        self._emit_progress(
            {
                "type": "status",
                "phase": "acting",
                "step": i,
                "actions": action_names,
                "message": f"第 {i} 步：执行 {', '.join(action_names)}…",
            }
        )
        for item in parsed.actions:
            if item.action != "ask_user" and self._ask_buffer:
                flush = self._flush_ask_buffer()
                step.calls.append(
                    ActionCall(
                        action="ask_user",
                        action_input='{"commit":true}',
                        observation=flush.output
                        if flush.ok
                        else f"ERROR: {flush.output}",
                        ok=flush.ok,
                        data=dict(flush.data or {}),
                    )
                )
                logger.notice(
                    f"ReAct 第 {i} 步先提交暂存 ask_user ok={flush.ok}"
                )

            skill_result = self._execute_action(mode, item.action, item.action_input)
            observation = (
                skill_result.output if skill_result.ok else f"ERROR: {skill_result.output}"
            )
            step.calls.append(
                ActionCall(
                    action=item.action,
                    action_input=item.action_input,
                    observation=observation,
                    ok=skill_result.ok,
                    data=dict(skill_result.data or {}),
                )
            )
            logger.notice(
                f"ReAct 第 {i} 步 Action={item.action} ok={skill_result.ok} "
                f"(mode={mode.value}, 共 {len(parsed.actions)} 个)"
            )

        observation_text = format_observations(step.calls)
        steps.append(step)
        self._emit_step(step)

        # doom-loop 检测（在落盘 observation 前）
        if hasattr(self, "_doom_tracker") and self._doom_tracker is not None:
            verdict = self._doom_tracker.observe(step)
            if verdict.triggered:
                if verdict.inject:
                    messages.append(
                        AIMessage(
                            role="user",
                            content=(
                                f"Observation:\n{observation_text}\n\n{verdict.inject}"
                            ),
                        )
                    )
                    return None
                messages.append(
                    AIMessage(
                        role="user",
                        content=f"Observation:\n{observation_text}",
                    )
                )
                return self._finalize_result(
                    ReActResult(
                        answer=verdict.message
                        or "检测到工具调用空转，已停止本轮任务。",
                        steps=steps,
                        completed=False,
                        messages=messages,
                        mode=mode.value,
                        stop_reason="doom_loop",
                    )
                )

        prefix = "Observations" if len(step.calls) > 1 else "Observation"
        messages.append(AIMessage(role="user", content=f"{prefix}:\n{observation_text}"))
        return None

    def _execute_action(
        self: ReActAgent,
        mode: AgentMode,
        skill_name: str,
        action_input: str | None,
    ) -> SkillResult:
        if mode is AgentMode.PLAN and not self._is_readonly_allowed(skill_name, action_input):
            reason = (
                f"Plan Mode 禁止执行非只读操作: {skill_name}。"
                f"请将写入类操作写入 Plan 步骤，而不是 Action。"
            )
            logger.warning(reason)
            return SkillResult(ok=False, output=reason)

        if skill_name == "ask_user":
            return self._buffer_ask_user(action_input)

        bridge = getattr(self, "bridge", None)
        if bridge is not None:
            return bridge.invoke(
                skill_name,
                action_input,
                permission=self.permission,
                control=getattr(self, "control", None),
                on_progress=self.on_progress,
            )

        return self.skills.run(
            skill_name,
            action_input,
            permission=self.permission,
        )

    def _is_readonly_allowed(
        self: ReActAgent, skill_name: str, action_input: str | None
    ) -> bool:
        if skill_name in self.plan_allow_skills:
            return True
        if not self.allow_readonly_in_plan:
            return False
        allowed_ops = self.readonly_actions.get(skill_name) or self.readonly_actions.get("*")
        if not allowed_ops:
            return False

        op = ""
        if action_input:
            try:
                data = json.loads(action_input)
                if isinstance(data, dict):
                    op = str(data.get("action") or "").strip().lower()
            except json.JSONDecodeError:
                op = ""
        if not op:
            return "*" in allowed_ops
        return op in allowed_ops or "*" in allowed_ops
