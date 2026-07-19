"""ReAct 框架层：Thought → Action(s) → Observation(s) 循环；支持 Plan Mode。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config as cfg
from ai import AIClient, AIMessage, create_ai_client
from log import get_logger
from permission import PermissionGuard
from react.ask_buffer import AskBufferMixin
from react.control import RunControl
from react.doom_loop import DoomLoopTracker, doom_tracker_from_config
from react.loop import LoopMixin
from react.mode import AgentMode
from react.plan import PLAN_SYSTEM_PROMPT, Plan
from react.progress import ProgressMixin
from react.step_extend import StepExtendMixin
from react.types import (
    DEFAULT_SYSTEM_PROMPT,
    ActionCall,
    DetailHandler,
    PlanResult,
    ProgressHandler,
    ReActResult,
    ReActStep,
    summarize_plan_execution,
)
from react.detail import DETAIL_OFF, parse_detail_level
from skills import SkillRegistry
from skills.bridge import SkillBridge

logger = get_logger("react")

# 兼容：历史代码 / tests 从 react.agent 导入类型
__all__ = [
    "ActionCall",
    "DetailHandler",
    "PlanResult",
    "ProgressHandler",
    "ReActAgent",
    "ReActResult",
    "ReActStep",
    "DEFAULT_SYSTEM_PROMPT",
]


class ReActAgent(ProgressMixin, AskBufferMixin, StepExtendMixin, LoopMixin):
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
        control: RunControl | None = None,
        stream_ai: bool | None = None,
    ) -> None:
        react_cfg = cfg.get_section("react", {}) or {}
        plan_cfg = react_cfg.get("plan") or {}
        ai_cfg = cfg.get_section("ai", {}) or {}
        deepseek_cfg = ai_cfg.get("deepseek") or {}

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
        self.bridge = SkillBridge(self.skills)

        configured_mode = mode if mode is not None else react_cfg.get("mode", AgentMode.AGENT.value)
        self.mode = AgentMode.parse(configured_mode)

        self.max_steps = max_steps if max_steps is not None else int(react_cfg.get("max_steps", 12))
        self.max_steps_hard_cap = int(react_cfg.get("max_steps_hard_cap", 96))
        raw_extend = react_cfg.get("step_extend_options") or [6, 12, 24]
        self.step_extend_options = [
            int(x) for x in raw_extend if str(x).strip().isdigit() and int(x) > 0
        ] or [6, 12, 24]
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
        raw_allow = (
            plan_allow_skills
            if plan_allow_skills is not None
            else (
                plan_cfg.get("allow_skills")
                or [
                    "ask_user",
                    "feishu_notify",
                    "request_capability",
                    "web_fetch",
                    "diff_review",
                    "todo_tracker",
                ]
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
        self.control = control if control is not None else RunControl()
        self.stream_ai = (
            stream_ai
            if stream_ai is not None
            else bool(deepseek_cfg.get("stream", True))
        )
        self._doom_tracker: DoomLoopTracker = doom_tracker_from_config(react_cfg)

        self._ask_buffer: list[dict[str, Any]] = []
        self._ask_seq = 0
        self._empty_nudge_count = 0
        self._live_messages: list[AIMessage] | None = None

        self._system_prompt_base = str(
            system_prompt or react_cfg.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        ).strip()
        self.system_prompt = self._format_runtime_prompt(self.mode)

    def set_detail(self, level: str) -> None:
        """运行时切换细节级别：off / summary / full。"""
        self.detail = parse_detail_level(level)

    def set_workdir(self, workdir: str | Path) -> Path:
        """设定 Agent 工作目录，并同步到带 root 的 Skill。"""
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
            control=self.control,
            stream_ai=self.stream_ai,
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
        self.control.begin_run()
        try:
            for plan_step in plan.steps:
                if self.control.cancel_requested:
                    return self._finalize_result(
                        ReActResult(
                            answer="已取消计划执行。",
                            steps=steps,
                            completed=False,
                            messages=messages,
                            mode=AgentMode.AGENT.value,
                            plan=plan,
                            stop_reason="cancelled",
                        )
                    )
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
                skill_result = self.bridge.invoke(
                    plan_step.skill,
                    plan_step.arguments or raw_input,
                    permission=self.permission,
                    control=self.control,
                    on_progress=self.on_progress,
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

            answer = summarize_plan_execution(steps)
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
        finally:
            self.control.end_run()

    def run_dict(self, task: str, **kwargs: Any) -> dict[str, Any]:
        result = self.run(task, **kwargs)
        payload: dict[str, Any] = {
            "answer": result.answer,
            "completed": result.completed,
            "mode": result.mode,
            "stop_reason": result.stop_reason,
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
