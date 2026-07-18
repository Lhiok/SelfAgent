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
from react.detail import (
    DETAIL_OFF,
    format_result_detail,
    format_step_detail,
    parse_detail_level,
)
from react.mode import AgentMode
from react.parser import parse_react_output
from react.plan import PLAN_SYSTEM_PROMPT, Plan, parse_plan
from skills import SkillRegistry, SkillResult

logger = get_logger("react")

DetailHandler = Callable[[str], None]

DEFAULT_SYSTEM_PROMPT = """你是一个可调用工具的助手。请严格使用 ReAct 格式回复：
Thought: 思考下一步
Action: 工具名
Action Input: 工具参数（JSON）
或在完成时输出：
Thought: 已得到答案
Final Answer: 最终结果

可以在同一次回复中连续输出多个 Action / Action Input（按顺序执行）。
工具之间互不依赖时可并行规划多个调用；需要上一步结果时再分步调用。
"""


@dataclass
class ActionCall:
    action: str
    action_input: str
    observation: str | None = None
    ok: bool | None = None


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
        observations: list[str] = []

        for plan_step in plan.steps:
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
                )
            )
            steps.append(react_step)
            self._emit_step(react_step)
            observations.append(
                f"[{plan_step.index}] {plan_step.skill}: {observation}"
            )
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

        answer = "计划执行完成\n" + "\n".join(observations)
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

        steps: list[ReActStep] = []
        logger.notice(f"ReAct 开始任务，mode={mode.value}，max_steps={self.max_steps}")

        for i in range(1, self.max_steps + 1):
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
                plan = None
                if mode is AgentMode.PLAN:
                    plan = parse_plan(
                        raw,
                        thought=parsed.thought,
                        final_answer=parsed.final_answer,
                    )
                steps.append(step)
                self._emit_step(step)
                logger.notice(f"ReAct 完成于第 {i} 步（mode={mode.value}）")
                return self._finalize_result(
                    ReActResult(
                        answer=parsed.final_answer,
                        steps=steps,
                        completed=True,
                        messages=messages,
                        mode=mode.value,
                        plan=plan,
                    )
                )

            if not parsed.actions:
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

            for item in parsed.actions:
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
        return self._finalize_result(
            ReActResult(
                answer="达到最大步数仍未得到 Final Answer",
                steps=steps,
                completed=False,
                messages=messages,
                mode=mode.value,
            )
        )

    def _emit_step(self, step: ReActStep) -> None:
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

        return self.skills.run(
            skill_name,
            action_input,
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
