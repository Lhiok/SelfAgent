"""ReAct 框架层：Thought → Action(s) → Observation(s) 循环；支持 Plan Mode。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import config as cfg
from ai import AIClient, AIMessage, create_ai_client
from log import get_logger
from permission import PermissionGuard
from react.changes import (
    collect_changes_from_steps,
    extract_change_from_call,
    merge_changes_by_path,
)
from react.detail import (
    DETAIL_OFF,
    format_result_detail,
    format_step_detail,
    parse_detail_level,
)
from react.mode import AgentMode
from react.parser import parse_react_output
from react.plan import (
    PLAN_SYSTEM_PROMPT,
    Plan,
    clean_plan_summary,
    parse_plan,
)
from skills import SkillRegistry, SkillResult
from skills.ask_user import coerce_questions

logger = get_logger("react")

DetailHandler = Callable[[str], None]
# 进度事件：{"type":"status"|"step", ...}，供 CLI/Web 实时展示
ProgressHandler = Callable[[dict[str, Any]], None]

DEFAULT_SYSTEM_PROMPT = """你是一个可调用工具的助手。请严格使用 ReAct 格式回复：
Thought: 思考下一步
Action: 工具名
Action Input: 工具参数（JSON）
或在完成时输出：
Thought: 已得到答案
Final Answer: 最终结果

可以在同一次回复中连续输出多个 Action / Action Input（按顺序执行）。
工具之间互不依赖时可并行规划多个调用；需要上一步结果时再分步调用。

关于 ask_user：
- 可连续多次调用 ask_user 收集问题；系统会先暂存，不会立刻打断用户。
- 当你准备输出 Plan / Final Answer 时，系统会把暂存的问题一次性展示给用户确认。
- 也可以在一次 ask_user 中用 questions 数组提出全部问题。
"""


@dataclass
class ActionCall:
    action: str
    action_input: str
    observation: str | None = None
    ok: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReActStep:
    index: int
    thought: str
    calls: list[ActionCall] = field(default_factory=list)
    final_answer: str | None = None
    raw_model_output: str = ""

    @property
    def action(self) -> str | None:
        return self.calls[0].action if self.calls else None

    @property
    def action_input(self) -> str | None:
        return self.calls[0].action_input if self.calls else None

    @property
    def observation(self) -> str | None:
        if not self.calls:
            return None
        if len(self.calls) == 1:
            return self.calls[0].observation
        return _format_observations(self.calls)


@dataclass
class ReActResult:
    answer: str
    steps: list[ReActStep] = field(default_factory=list)
    completed: bool = False
    messages: list[AIMessage] = field(default_factory=list)
    mode: str = AgentMode.AGENT.value
    plan: Plan | None = None
    # 当 react.detail != off 时填充，便于调用方直接展示
    detail_text: str = ""
    detail_level: str = DETAIL_OFF

    def format_detail(
        self,
        level: str | None = None,
        *,
        max_chars: int = 2000,
    ) -> str:
        """按级别格式化过程细节（默认用本次运行的 detail_level）。"""
        use = level if level is not None else (
            self.detail_level if self.detail_level != DETAIL_OFF else "full"
        )
        return format_result_detail(self, use, max_chars=max_chars)


@dataclass
class PlanResult:
    """Plan Mode 专用结果。"""

    plan: Plan
    answer: str
    steps: list[ReActStep] = field(default_factory=list)
    completed: bool = False
    messages: list[AIMessage] = field(default_factory=list)
    detail_text: str = ""
    detail_level: str = DETAIL_OFF

    def to_react_result(self) -> ReActResult:
        return ReActResult(
            answer=self.answer,
            steps=self.steps,
            completed=self.completed,
            messages=self.messages,
            mode=AgentMode.PLAN.value,
            plan=self.plan,
            detail_text=self.detail_text,
            detail_level=self.detail_level,
        )


def _format_observations(calls: list[ActionCall]) -> str:
    if len(calls) == 1:
        return calls[0].observation or ""
    lines: list[str] = []
    for i, call in enumerate(calls, start=1):
        status = "ok" if call.ok else "error"
        lines.append(
            f"[{i}] Action: {call.action} ({status})\n"
            f"Observation: {call.observation or ''}"
        )
    return "\n\n".join(lines)


def _summarize_plan_execution(steps: list[ReActStep]) -> str:
    """生成简洁的计划执行摘要（同文件多处改动合并，避免重复罗列）。"""
    changes = collect_changes_from_steps(steps)
    step_n = len(steps)
    ok_n = sum(1 for s in steps if s.calls and s.calls[0].ok is not False)

    if changes:
        patch_n = sum(int(c.get("patch_count") or 1) for c in changes)
        head = f"计划执行完成：{len(changes)} 个文件"
        if patch_n > len(changes):
            head += f"，{patch_n} 处改动"
        elif step_n > len(changes):
            head += f"（共 {step_n} 步）"
        lines = [head]
        for ch in changes:
            path = str(ch.get("path") or "").replace("\\", "/")
            kind = str(ch.get("kind") or "patch")
            label = {
                "write": "新建",
                "overwrite": "覆盖",
                "patch": "修改",
                "move": "移动",
            }.get(kind, kind)
            n = int(ch.get("patch_count") or 1)
            if kind == "move" and ch.get("dest"):
                dest = str(ch.get("dest")).replace("\\", "/")
                lines.append(f"· {label} {path} → {dest}")
            elif n > 1:
                lines.append(f"· {label} {path}（{n} 处）")
            else:
                lines.append(f"· {label} {path}")
        return "\n".join(lines)

    if ok_n == step_n:
        return f"计划执行完成（{step_n} 步）"
    return f"计划执行完成：成功 {ok_n}/{step_n} 步"


class ReActAgent:
    def __init__(
        self,
        *,
        ai: AIClient | None = None,
        skills: SkillRegistry | None = None,
        permission: PermissionGuard | None = None,
        role: str | None = None,
        mode: str | AgentMode | None = None,
        max_steps: int | None = None,
        system_prompt: str | None = None,
        provider: str | None = None,
        allow_readonly_in_plan: bool | None = None,
        readonly_actions: dict[str, list[str]] | None = None,
        plan_allow_skills: list[str] | None = None,
        detail: str | None = None,
        stream_detail: bool | None = None,
        detail_max_chars: int | None = None,
        on_detail: DetailHandler | None = None,
        on_progress: ProgressHandler | None = None,
        workdir: str | Path | None = None,
    ) -> None:
        react_cfg = cfg.get_section("react", {}) or {}
        plan_cfg = react_cfg.get("plan") or {}

        self.ai = ai or create_ai_client(provider=provider)

        if permission is not None:
            self.permission = permission
            if role:
                self.permission.use_role(role)
        elif skills is not None and skills.permission is not None:
            self.permission = skills.permission
            if role:
                self.permission.use_role(role)
        else:
            try:
                self.permission = PermissionGuard.from_config(role=role)
            except Exception:  # noqa: BLE001
                self.permission = PermissionGuard.allow_all()

        raw_workdir = workdir if workdir is not None else react_cfg.get("workdir")
        if isinstance(raw_workdir, str) and not raw_workdir.strip():
            raw_workdir = None

        if skills is None:
            self.skills = SkillRegistry.from_config(
                permission=self.permission,
                load_permission=False,
                workdir=raw_workdir,
            )
        else:
            self.skills = skills
            if raw_workdir is not None:
                self.skills.set_workdir(raw_workdir)
        self.skills.set_permission(self.permission)
        self.workdir = self._infer_workdir()

        configured_mode = mode if mode is not None else react_cfg.get("mode", AgentMode.AGENT.value)
        self.mode = AgentMode.parse(configured_mode)

        self.max_steps = max_steps if max_steps is not None else int(react_cfg.get("max_steps", 12))
        self.allow_readonly_in_plan = (
            allow_readonly_in_plan
            if allow_readonly_in_plan is not None
            else bool(plan_cfg.get("allow_readonly_tools", True))
        )
        self.readonly_actions = {
            str(k): {str(a).lower() for a in (v or [])}
            for k, v in (
                readonly_actions
                if readonly_actions is not None
                else (plan_cfg.get("readonly_actions") or {"local_file": ["list", "read"]})
            ).items()
        }
        # Plan Mode 下始终允许的交互类 Skill（如 ask_user）
        raw_allow = (
            plan_allow_skills
            if plan_allow_skills is not None
            else (
                plan_cfg.get("allow_skills")
                or ["ask_user", "feishu_notify", "request_capability"]
            )
        )
        self.plan_allow_skills = {str(s).strip() for s in raw_allow if str(s).strip()}

        self.detail = parse_detail_level(
            detail if detail is not None else react_cfg.get("detail", DETAIL_OFF)
        )
        self.stream_detail = (
            stream_detail
            if stream_detail is not None
            else bool(react_cfg.get("stream_detail", True))
        )
        self.detail_max_chars = (
            detail_max_chars
            if detail_max_chars is not None
            else int(react_cfg.get("detail_max_chars", 2000))
        )
        self.on_detail = on_detail
        self.on_progress = on_progress
        # ask_user 跨步暂存；输出 Plan/Final Answer 或遇到其它工具前再一次性询问
        self._ask_buffer: list[dict[str, Any]] = []
        self._ask_seq = 0
        # 当前 run 的消息列表引用，供 web ask 落盘检查点
        self._live_messages: list[AIMessage] | None = None

        self._system_prompt_base = str(
            system_prompt or react_cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        ).strip()
        self.system_prompt = self._format_runtime_prompt(self.mode)

    def set_detail(self, level: str) -> None:
        """运行时切换细节级别：off / summary / full。"""
        self.detail = parse_detail_level(level)

    def set_workdir(self, workdir: str | Path) -> Path:
        """设定 Agent 工作目录，并同步到 local_file / search_code / shell_run / git_ops。"""
        path = self.skills.set_workdir(workdir)
        self.workdir = path
        self.system_prompt = self._format_runtime_prompt(self.mode)
        logger.notice(f"Agent 工作目录: {path}")
        return path

    def with_mode(self, mode: str | AgentMode) -> "ReActAgent":
        """切换模式（返回新实例，复用 ai/skills/permission）。"""
        return ReActAgent(
            ai=self.ai,
            skills=self.skills,
            permission=self.permission,
            mode=mode,
            max_steps=self.max_steps,
            allow_readonly_in_plan=self.allow_readonly_in_plan,
            readonly_actions={k: sorted(v) for k, v in self.readonly_actions.items()},
            plan_allow_skills=sorted(self.plan_allow_skills),
            detail=self.detail,
            stream_detail=self.stream_detail,
            detail_max_chars=self.detail_max_chars,
            on_detail=self.on_detail,
            on_progress=self.on_progress,
            workdir=self.workdir,
            system_prompt=self._system_prompt_base,
        )

    def run(self, task: str, *, history: list[AIMessage] | None = None) -> ReActResult:
        if self.mode is AgentMode.PLAN:
            return self.plan(task, history=history).to_react_result()
        return self._run_agent(task, history=history)

    def plan(self, task: str, *, history: list[AIMessage] | None = None) -> PlanResult:
        """Plan Mode：调研（可选只读）并产出计划，不执行写入。"""
        logger.notice("进入 Plan Mode")
        result = self._run_loop(
            task,
            history=history,
            mode=AgentMode.PLAN,
            system_prompt=self._build_prompt(AgentMode.PLAN),
        )
        plan = result.plan or Plan(summary=result.answer, thought="")
        return PlanResult(
            plan=plan,
            answer=result.answer,
            steps=result.steps,
            completed=result.completed,
            messages=result.messages,
            detail_text=result.detail_text,
            detail_level=result.detail_level,
        )

    def execute_plan(
        self,
        plan: Plan,
        *,
        history: list[AIMessage] | None = None,
        stop_on_error: bool = True,
    ) -> ReActResult:
        """按已批准的计划逐步执行 Skill（走权限校验）。"""
        if not plan.steps:
            return self._finalize_result(
                ReActResult(
                    answer="计划为空，无可执行步骤",
                    completed=False,
                    mode=AgentMode.AGENT.value,
                    plan=plan,
                )
            )

        logger.notice(f"开始执行计划，共 {len(plan.steps)} 步")
        steps: list[ReActStep] = []
        messages: list[AIMessage] = list(history or [])

        for plan_step in plan.steps:
            self._emit_progress(
                {
                    "type": "status",
                    "phase": "acting",
                    "step": plan_step.index,
                    "actions": [plan_step.skill],
                    "message": f"执行计划第 {plan_step.index} 步：{plan_step.skill}…",
                }
            )
            react_step = ReActStep(
                index=plan_step.index,
                thought=plan_step.why or f"执行计划第 {plan_step.index} 步",
            )
            raw_input = plan_step.raw_input or json.dumps(
                plan_step.arguments, ensure_ascii=False
            )
            skill_result = self.skills.run(
                plan_step.skill,
                plan_step.arguments or raw_input,
                permission=self.permission,
            )
            observation = (
                skill_result.output if skill_result.ok else f"ERROR: {skill_result.output}"
            )
            react_step.calls.append(
                ActionCall(
                    action=plan_step.skill,
                    action_input=raw_input,
                    observation=observation,
                    ok=skill_result.ok,
                    data=dict(skill_result.data or {}),
                )
            )
            steps.append(react_step)
            self._emit_step(react_step)
            logger.notice(
                f"计划步骤 {plan_step.index} skill={plan_step.skill} ok={skill_result.ok}"
            )
            if stop_on_error and not skill_result.ok:
                answer = (
                    f"计划执行中断于第 {plan_step.index} 步: {skill_result.output}"
                )
                return self._finalize_result(
                    ReActResult(
                        answer=answer,
                        steps=steps,
                        completed=False,
                        messages=messages,
                        mode=AgentMode.AGENT.value,
                        plan=plan,
                    )
                )

        answer = _summarize_plan_execution(steps)
        return self._finalize_result(
            ReActResult(
                answer=answer,
                steps=steps,
                completed=True,
                messages=messages,
                mode=AgentMode.AGENT.value,
                plan=plan,
            )
        )

    def run_dict(self, task: str, **kwargs: Any) -> dict[str, Any]:
        result = self.run(task, **kwargs)
        payload: dict[str, Any] = {
            "answer": result.answer,
            "completed": result.completed,
            "mode": result.mode,
            "steps": [
                {
                    "index": s.index,
                    "thought": s.thought,
                    "action": s.action,
                    "action_input": s.action_input,
                    "observation": s.observation,
                    "final_answer": s.final_answer,
                    "calls": [
                        {
                            "action": c.action,
                            "action_input": c.action_input,
                            "observation": c.observation,
                            "ok": c.ok,
                        }
                        for c in s.calls
                    ],
                }
                for s in result.steps
            ],
        }
        if result.plan is not None:
            payload["plan"] = result.plan.to_dict()
        return payload

    def _run_agent(self, task: str, *, history: list[AIMessage] | None = None) -> ReActResult:
        return self._run_loop(
            task,
            history=history,
            mode=AgentMode.AGENT,
            system_prompt=self._build_prompt(AgentMode.AGENT),
        )

    def _infer_workdir(self) -> Path:
        if self.skills.workdir is not None:
            return self.skills.workdir
        local = self.skills.get("local_file")
        if local is not None and hasattr(local, "root"):
            return Path(local.root).resolve()
        return Path.cwd().resolve()

    def _format_runtime_prompt(self, mode: AgentMode) -> str:
        base_prompt = self._system_prompt_base
        if mode is AgentMode.PLAN:
            base_prompt = f"{base_prompt}\n\n{PLAN_SYSTEM_PROMPT.strip()}"
        return (
            f"{base_prompt}\n\n"
            f"当前模式: {mode.value}\n"
            f"当前工作目录: {self.workdir}\n"
            f"当前权限角色: {self.permission.role}\n"
            f"可用工具:\n{self.skills.list_schemas(self.permission)}"
        )

    def _build_prompt(self, mode: AgentMode) -> str:
        return self._format_runtime_prompt(mode)

    def continue_from_messages(
        self,
        messages: list[AIMessage],
        *,
        observation: str,
        mode: str | AgentMode | None = None,
    ) -> ReActResult:
        """从已落盘的消息续跑（用于重启后恢复 ask_user）。"""
        run_mode = AgentMode.parse(mode) if mode is not None else self.mode
        msgs = [
            AIMessage(role=m.role, content=m.content, name=m.name)
            for m in (messages or [])
            if m.role in {"system", "user", "assistant", "tool"}
        ]
        prompt = self._build_prompt(run_mode)
        if msgs and msgs[0].role == "system":
            msgs[0] = AIMessage(role="system", content=prompt)
        else:
            msgs.insert(0, AIMessage(role="system", content=prompt))
        obs = (observation or "").strip()
        obs_msg = AIMessage(
            role="user",
            content=obs if obs.startswith("Observation") else f"Observation:\n{obs}",
        )
        if msgs and msgs[-1].role == "user" and str(msgs[-1].content).startswith(
            "Observation"
        ):
            msgs[-1] = obs_msg
        else:
            msgs.append(obs_msg)
        return self._run_loop_from_messages(msgs, mode=run_mode)

    def _run_loop(
        self,
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
        self,
        messages: list[AIMessage],
        *,
        mode: AgentMode,
    ) -> ReActResult:
        steps: list[ReActStep] = []
        self._ask_buffer.clear()
        self._ask_seq = 0
        self._live_messages = messages
        logger.notice(f"ReAct 开始任务，mode={mode.value}，max_steps={self.max_steps}")

        try:
            return self._run_loop_body(messages, mode=mode, steps=steps)
        finally:
            self._live_messages = None

    def _run_loop_body(
        self,
        messages: list[AIMessage],
        *,
        mode: AgentMode,
        steps: list[ReActStep],
    ) -> ReActResult:
        for i in range(1, self.max_steps + 1):
            self._emit_progress(
                {
                    "type": "status",
                    "phase": "thinking",
                    "step": i,
                    "message": f"第 {i} 步：模型思考中…",
                }
            )
            try:
                response = self.ai.chat(messages)
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
            raw = response.content.strip()
            parsed = parse_react_output(raw)
            step = ReActStep(
                index=i,
                thought=parsed.thought,
                final_answer=parsed.final_answer,
                raw_model_output=raw,
            )

            messages.append(AIMessage(role="assistant", content=raw))

            if parsed.final_answer is not None:
                if self._ask_buffer:
                    # 先一次性询问暂存问题；丢掉「把问卷写进 Final Answer」的脏消息
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
                    continue

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
                    # 无 skill= 计划时：仍按普通 Final Answer 展示，不转入 ask_user
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
                # 有暂存问题却未输出标准 Final Answer/Action：先弹窗问用户，勿直接结束
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
                    continue

                logger.warning(f"第 {i} 步未解析到 Action/Final Answer，结束循环")
                fallback = parsed.thought or raw
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
                # 遇到其它工具前，先把暂存的 ask_user 一次性问完
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

            observation_text = _format_observations(step.calls)
            steps.append(step)
            self._emit_step(step)
            prefix = "Observations" if len(step.calls) > 1 else "Observation"
            messages.append(AIMessage(role="user", content=f"{prefix}:\n{observation_text}"))

        logger.warning("ReAct 达到最大步数仍未结束")
        # 避免步数用尽时暂存问题永远不弹出
        if self._ask_buffer:
            flush = self._flush_ask_buffer()
            steps.append(
                ReActStep(
                    index=len(steps) + 1,
                    thought="达到最大步数，先请用户确认暂存问题",
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
            )
            self._emit_step(steps[-1])
            return self._finalize_result(
                ReActResult(
                    answer=(
                        flush.output
                        if flush.ok
                        else "达到最大步数，且确认问题失败"
                    ),
                    steps=steps,
                    completed=False,
                    messages=messages,
                    mode=mode.value,
                )
            )
        return self._finalize_result(
            ReActResult(
                answer="达到最大步数仍未得到 Final Answer",
                steps=steps,
                completed=False,
                messages=messages,
                mode=mode.value,
            )
        )

    def _emit_progress(self, event: dict[str, Any]) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"on_progress 回调失败: {exc}")

    def _step_progress_payload(self, step: ReActStep) -> dict[str, Any]:
        level = self.detail if self.detail != DETAIL_OFF else "summary"
        max_chars = self.detail_max_chars
        actions: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        for call in step.calls:
            item: dict[str, Any] = {
                "action": call.action,
                "ok": call.ok,
            }
            # 解析 local_file 的子操作名，便于 UI 显示 write/patch
            try:
                payload = json.loads(call.action_input or "")
                if isinstance(payload, dict) and payload.get("action"):
                    item["op"] = str(payload.get("action"))
                    if payload.get("path"):
                        item["path"] = str(payload.get("path")).replace("\\", "/")
            except (TypeError, json.JSONDecodeError):
                pass
            inp = (call.action_input or "").strip()
            if inp:
                item["input"] = inp if len(inp) <= 240 else inp[:239] + "…"
            obs = (call.observation or "").strip()
            if obs:
                item["observation"] = (
                    obs if len(obs) <= max_chars else obs[: max_chars - 1] + "…"
                )
            change = extract_change_from_call(
                skill_name=call.action,
                action_input=call.action_input,
                data=call.data,
                ok=call.ok,
            )
            if change:
                item["change"] = change
                changes.append(change)
            actions.append(item)
        return {
            "type": "step",
            "index": step.index,
            "thought": (step.thought or "").strip(),
            "actions": actions,
            "changes": merge_changes_by_path(changes, max_chars=max_chars),
            "final_answer": step.final_answer,
            "text": format_step_detail(step, level, max_chars=max_chars),
        }

    def _emit_step(self, step: ReActStep) -> None:
        # 结构化进度始终推送（与 detail 开关无关），供 Web/CLI 实时展示
        self._emit_progress(self._step_progress_payload(step))

        if self.detail == DETAIL_OFF:
            return
        text = format_step_detail(
            step, self.detail, max_chars=self.detail_max_chars
        )
        if not text:
            return
        # 始终落盘到本次运行日志；控制台/回调仅在 stream_detail 时输出
        logger.notice(f"ReAct 细节\n{text}")
        if not self.stream_detail:
            return
        if self.on_detail is not None:
            self.on_detail(text)
        else:
            print(text, flush=True)

    def _finalize_result(self, result: ReActResult) -> ReActResult:
        result.detail_level = self.detail
        if self.detail != DETAIL_OFF:
            result.detail_text = format_result_detail(
                result, self.detail, max_chars=self.detail_max_chars
            )
        return result

    def _execute_action(
        self,
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

        return self.skills.run(
            skill_name,
            action_input,
            permission=self.permission,
        )

    def _parse_action_input(self, action_input: str | None) -> dict[str, Any]:
        if not action_input or not str(action_input).strip():
            return {}
        try:
            data = json.loads(action_input)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _buffer_ask_user(self, action_input: str | None) -> SkillResult:
        """暂存问题，不立即弹 UI；等 commit / Final Answer / 其它工具前再一次性询问。"""
        args = self._parse_action_input(action_input)
        if args.get("commit") or args.get("flush"):
            return self._flush_ask_buffer()

        items = coerce_questions(args)
        if not items:
            return SkillResult(
                ok=False,
                output="ask_user 需要 question+options，或非空 questions 数组",
            )

        for item in items:
            self._ask_seq += 1
            buffered = dict(item)
            buffered["id"] = str(self._ask_seq)
            self._ask_buffer.append(buffered)

        total = len(self._ask_buffer)
        added = len(items)
        logger.notice(f"ask_user 已暂存 {added} 题（合计 {total}）")
        self._emit_progress(
            {
                "type": "status",
                "phase": "ask_buffer",
                "message": f"已收集 {total} 个待确认问题…",
            }
        )
        return SkillResult(
            ok=True,
            output=(
                f"已暂存 {added} 个问题（合计 {total} 个，尚未询问用户）。"
                "请继续用 ask_user 补充其余待确认项；"
                "全部收集完后必须输出以 `Final Answer:` 开头的简短说明"
                "（可同时带 Plan: skill=... 步骤），"
                "系统会立即请用户确认上述全部问题。"
                "不要用不带 Final Answer: 前缀的纯文本结束。"
            ),
            data={"buffered": total, "added": added},
        )

    def _flush_ask_buffer(self) -> SkillResult:
        if not self._ask_buffer:
            return SkillResult(ok=True, output="没有待确认的暂存问题", data={"questions": []})

        items = list(self._ask_buffer)
        self._ask_buffer.clear()
        logger.notice(f"ask_user 一次性提交 {len(items)} 题给用户")
        self._emit_progress(
            {
                "type": "status",
                "phase": "ask_user",
                "message": f"等待你确认 {len(items)} 个问题…",
            }
        )
        # 直接走 Skill，避免再次进入暂存逻辑
        return self.skills.run(
            "ask_user",
            {"questions": items},
            permission=self.permission,
        )

    def _is_readonly_allowed(self, skill_name: str, action_input: str | None) -> bool:
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
            # 无 action 字段时，仅当白名单含 * 才放行
            return "*" in allowed_ops
        return op in allowed_ops or "*" in allowed_ops
