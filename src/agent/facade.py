"""Agent 门面：委托 AgentEngine（原生 tool_calls 循环）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config as cfg
from agent.engine import AgentEngine
from agent.tools import DEFAULT_TOOL_SYSTEM_PROMPT
from agent.types import LoopState
from ai import AIClient, AIMessage, create_ai_client
from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from session.detail import DETAIL_OFF, parse_detail_level
from session.doom_loop import DoomLoopTracker, doom_tracker_from_config
from session.mode import AgentMode
from plan.model import Plan
from session.progress import ProgressMixin
from session.step_extend import StepExtendMixin
from session.types import (
    ActionCall,
    DetailHandler,
    PlanResult,
    ProgressHandler,
    AgentResult,
    AgentStep,
    summarize_plan_execution,
)
from skills import SkillRegistry
from skills.bridge import SkillBridge

logger = get_logger("session")

DEFAULT_SYSTEM_PROMPT = DEFAULT_TOOL_SYSTEM_PROMPT

__all__ = [
    "ActionCall",
    "DetailHandler",
    "PlanResult",
    "ProgressHandler",
    "Agent",
    "AgentResult",
    "AgentStep",
    "DEFAULT_SYSTEM_PROMPT",
]


class Agent(ProgressMixin, StepExtendMixin):
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
        self.mcp_manager = self._load_mcp_manager()
        self.session_id = ""
        self.bridge = SkillBridge(self.skills, mcp_manager=self.mcp_manager)

        configured_mode = mode if mode is not None else react_cfg.get("mode", AgentMode.AGENT.value)
        self.mode = AgentMode.parse(configured_mode)

        self.max_steps = max_steps if max_steps is not None else int(react_cfg.get("max_steps", 12))
        self.max_steps_hard_cap = int(react_cfg.get("max_steps_hard_cap", 96))
        self.max_tool_concurrency = int(react_cfg.get("max_tool_concurrency", 4))
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
                    "memory_ops",
                ]
            )
        )
        self.plan_allow_skills = {str(s).strip() for s in raw_allow if str(s).strip()}

        self.permission.bind_context(
            workdir=self.workdir,
            allow_readonly_in_plan=self.allow_readonly_in_plan,
            readonly_actions=self.readonly_actions,
            plan_allow_skills=self.plan_allow_skills,
            plan_active=self.mode is AgentMode.PLAN,
        )

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
        self._live_messages: list[AIMessage] | None = None
        self.memory_service: Any | None = None
        self.plan_lifecycle: Any | None = None
        self.bridge.bind_subagent_context(
            ai=self.ai,
            skills=self.skills,
            permission=self.permission,
            workdir=self.workdir,
            session_id=self.session_id,
            on_progress=self.on_progress,
            mcp_manager=self.mcp_manager,
        )

        self._system_prompt_base = str(
            system_prompt or react_cfg.get("system_prompt") or DEFAULT_TOOL_SYSTEM_PROMPT
        ).strip()
        self.system_prompt = self._format_runtime_prompt(self.mode)

    def set_detail(self, level: str) -> None:
        self.detail = parse_detail_level(level)

    def set_workdir(self, workdir: str | Path) -> Path:
        path = self.skills.set_workdir(workdir)
        self.workdir = path
        self.permission.bind_context(workdir=path)
        self.system_prompt = self._format_runtime_prompt(self.mode)
        if self.memory_service is not None:
            try:
                self.memory_service.set_workdir(path)
            except Exception:  # noqa: BLE001
                pass
        logger.notice(f"Agent 工作目录: {path}")
        return path

    def with_mode(self, mode: str | AgentMode) -> "Agent":
        clone = Agent(
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
        clone.memory_service = self.memory_service
        clone.plan_lifecycle = self.plan_lifecycle
        clone.mcp_manager = self.mcp_manager
        clone.session_id = self.session_id
        clone.bridge.set_mcp_manager(self.mcp_manager)
        clone.bridge.bind_subagent_context(
            ai=clone.ai,
            skills=clone.skills,
            permission=clone.permission,
            workdir=clone.workdir,
            session_id=clone.session_id,
            on_progress=clone.on_progress,
            mcp_manager=clone.mcp_manager,
        )
        return clone

    def run(self, task: str, *, history: list[AIMessage] | None = None) -> AgentResult:
        if self.mode is AgentMode.PLAN:
            return self.plan(task, history=history).to_agent_result()
        return self._run_via_engine(task, history=history, mode=AgentMode.AGENT)

    def plan(self, task: str, *, history: list[AIMessage] | None = None) -> PlanResult:
        logger.notice("进入 Plan Mode")
        result = self._run_via_engine(task, history=history, mode=AgentMode.PLAN)
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
        workflow_run: Any | None = None,
    ) -> AgentResult:
        if not plan.steps:
            return self._finalize_result(
                AgentResult(
                    answer="计划为空，无可执行步骤",
                    completed=False,
                    mode=AgentMode.AGENT.value,
                    plan=plan,
                )
            )

        from workflow.runner import WorkflowRunner, plan_to_workflow_run
        from workflow.types import RunStatus, StepStatus, WorkflowRun

        logger.notice(f"开始执行计划，共 {len(plan.steps)} 步（workflow runner）")
        self.permission.set_plan_active(False)
        messages: list[AIMessage] = list(history or [])
        wf_cfg = cfg.get_section("workflow", {}) or {}
        runs_base = Path(wf_cfg.get("workflows_dir", ".selfagent/workflows"))
        if workflow_run is not None and isinstance(workflow_run, WorkflowRun):
            run = workflow_run
        else:
            run = plan_to_workflow_run(plan)
        run.on_error = "stop" if stop_on_error else "continue"
        runner = WorkflowRunner(
            self.bridge,
            self.permission,
            control=self.control,
            on_progress=self.on_progress,
            runs_base=runs_base,
            agent_factory=lambda: self,
        )
        self.control.begin_run()
        try:
            finished = runner.run_sync(run)
        finally:
            self.control.end_run()

        steps: list[AgentStep] = []
        plan_by_sid = {f"s{ps.index}": ps for ps in plan.steps}
        for ws in finished.steps:
            ps = plan_by_sid.get(ws.id)
            idx = ps.index if ps is not None else (len(steps) + 1)
            raw_input = (
                (ps.raw_input if ps else "")
                or json.dumps((ps.arguments if ps else ws.input) or {}, ensure_ascii=False)
            )
            if ws.status == StepStatus.SKIPPED:
                continue
            if ws.status == StepStatus.CANCELLED:
                continue
            step = AgentStep(
                index=idx,
                thought=(ps.why if ps else ws.why)
                or f"执行计划第 {idx} 步",
            )
            step.calls.append(
                ActionCall(
                    action=(ps.skill if ps else ws.skill) or "",
                    action_input=raw_input,
                    observation=ws.output,
                    ok=ws.ok,
                    data={},
                )
            )
            steps.append(step)
            self._emit_step(step)

        if finished.status == RunStatus.CANCELLED:
            return self._finalize_result(
                AgentResult(
                    answer="已取消计划执行。",
                    steps=steps,
                    completed=False,
                    messages=messages,
                    mode=AgentMode.AGENT.value,
                    plan=plan,
                    stop_reason="cancelled",
                    workflow_run_id=finished.id,
                )
            )

        if finished.status != RunStatus.DONE:
            failed = next(
                (s for s in finished.steps if s.status == StepStatus.FAILED),
                None,
            )
            if failed is not None:
                answer = f"计划执行中断于步骤 {failed.id}: {failed.output}"
            else:
                answer = f"计划执行失败（{finished.status.value}）"
            return self._finalize_result(
                AgentResult(
                    answer=answer,
                    steps=steps,
                    completed=False,
                    messages=messages,
                    mode=AgentMode.AGENT.value,
                    plan=plan,
                    workflow_run_id=finished.id,
                )
            )

        answer = summarize_plan_execution(steps)
        return self._finalize_result(
            AgentResult(
                answer=answer,
                steps=steps,
                completed=True,
                messages=messages,
                mode=AgentMode.AGENT.value,
                plan=plan,
                workflow_run_id=finished.id,
            )
        )

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

    def continue_from_messages(
        self,
        messages: list[AIMessage],
        *,
        observation: str,
        mode: str | AgentMode | None = None,
    ) -> AgentResult:
        run_mode = AgentMode.parse(mode) if mode is not None else self.mode
        msgs = [
            AIMessage(
                role=m.role,
                content=m.content,
                name=m.name,
                tool_call_id=getattr(m, "tool_call_id", None),
                tool_calls=list(getattr(m, "tool_calls", None) or []),
            )
            for m in (messages or [])
            if m.role in {"system", "user", "assistant", "tool"}
        ]
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
        return self._run_via_engine("", history=None, mode=run_mode, messages=msgs)

    def _run_via_engine(
        self,
        task: str,
        *,
        history: list[AIMessage] | None,
        mode: AgentMode,
        messages: list[AIMessage] | None = None,
    ) -> AgentResult:
        reject_fb = ""
        if self.plan_lifecycle is not None:
            try:
                reject_fb = str(
                    self.plan_lifecycle.session.last_reject_feedback or ""
                )
            except Exception:  # noqa: BLE001
                reject_fb = ""
        engine = AgentEngine(
            ai=self.ai,
            skills=self.skills,
            permission=self.permission,
            bridge=self.bridge,
            control=self.control,
            on_progress=self.on_progress,
            max_steps=self.max_steps,
            max_concurrency=self.max_tool_concurrency,
            system_prompt=self._system_prompt_base,
            doom=self._doom_tracker,
            detail_level=self.detail,
            detail_max_chars=self.detail_max_chars,
            workdir=self.workdir,
            step_extend=self._engine_step_extend,
            on_bind_messages=self._bind_live_messages,
            on_step=self._emit_step,
            memory_service=self.memory_service,
            plan_reject_feedback=reject_fb,
            on_enter_plan=self._on_enter_plan,
            mcp_manager=self.mcp_manager,
            session_id=self.session_id,
        )
        result = engine.submit(
            task,
            history=history,
            mode=mode,
            messages=messages,
        )
        return self._finalize_result(result)

    def _on_enter_plan(self, reason: str = "") -> None:
        pre = self.mode
        if self.plan_lifecycle is not None:
            try:
                self.plan_lifecycle.enter(pre)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"plan_lifecycle.enter 失败: {exc}")
        self.mode = AgentMode.PLAN
        self.permission.set_plan_active(True)
        self.system_prompt = self._format_runtime_prompt(AgentMode.PLAN)
        if reason:
            logger.notice(f"enter_plan_mode: {reason}")
        else:
            logger.notice("enter_plan_mode")

    def _engine_step_extend(self, state: LoopState) -> bool:
        delta = self._prompt_extend_steps(
            steps=state.steps,
            messages=state.messages,
            used=state.turn_count,
            budget=state.max_turns,
            hard_cap=self.max_steps_hard_cap,
        )
        if delta is None or delta <= 0:
            return False
        state.max_turns = min(self.max_steps_hard_cap, state.max_turns + int(delta))
        logger.notice(f"用户延长步数 +{delta}，新上限={state.max_turns}")
        return True

    def _bind_live_messages(self, messages: list[AIMessage] | None) -> None:
        self._live_messages = messages

    def _infer_workdir(self) -> Path:
        if self.skills.workdir is not None:
            return self.skills.workdir
        local = self.skills.get("local_file")
        if local is not None and hasattr(local, "root"):
            return Path(local.root).resolve()
        return Path.cwd().resolve()

    def _load_mcp_manager(self) -> Any:
        mcp_cfg = cfg.get_section("mcp", {}) or {}
        if not mcp_cfg or not mcp_cfg.get("servers"):
            return None
        try:
            from mcp import McpManager

            return McpManager.from_config(mcp_cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"MCP 初始化失败: {exc}")
            return None

    def set_session_id(self, session_id: str) -> None:
        self.session_id = session_id or ""
        self.bridge.bind_subagent_context(session_id=self.session_id)

    def _format_runtime_prompt(self, mode: AgentMode) -> str:
        # 展示用；真正 system 由 AgentEngine 再拼一次
        return (
            f"{self._system_prompt_base}\n\n"
            f"当前模式: {mode.value}\n"
            f"当前工作目录: {self.workdir}\n"
            f"当前权限角色: {self.permission.role}\n"
        )

    def _build_prompt(self, mode: AgentMode) -> str:
        return self._system_prompt_base
