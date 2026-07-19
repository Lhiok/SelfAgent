"""AgentEngine：会话级提交入口（对齐 QueryEngine）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ai import AIClient, AIMessage
from agent.compat import state_to_agent_result
from agent.loop import build_schemas_for_mode, query_loop
from agent.tools import DEFAULT_TOOL_SYSTEM_PROMPT
from agent.types import LoopState
from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from session.detail import DETAIL_OFF
from session.doom_loop import DoomLoopTracker, doom_tracker_from_config
from session.mode import AgentMode
from session.types import ProgressHandler, AgentResult
from skills.bridge import SkillBridge
from skills.registry import SkillRegistry

logger = get_logger("agent.engine")


class AgentEngine:
    def __init__(
        self,
        *,
        ai: AIClient,
        skills: SkillRegistry,
        permission: PermissionGuard,
        bridge: SkillBridge | None = None,
        control: RunControl | None = None,
        on_progress: ProgressHandler | None = None,
        max_steps: int = 12,
        max_concurrency: int = 4,
        system_prompt: str = DEFAULT_TOOL_SYSTEM_PROMPT,
        doom: DoomLoopTracker | None = None,
        detail_level: str = DETAIL_OFF,
        detail_max_chars: int = 2000,
        workdir: Path | None = None,
        step_extend: Callable[[LoopState], bool] | None = None,
        on_bind_messages: Callable[[list[AIMessage] | None], None] | None = None,
        on_step: Callable[[Any], None] | None = None,
        memory_service: Any | None = None,
        plan_reject_feedback: str = "",
        on_enter_plan: Callable[[str], None] | None = None,
        mcp_manager: Any | None = None,
        session_id: str = "",
        include_run_subagent: bool = True,
    ) -> None:
        self.ai = ai
        self.skills = skills
        self.permission = permission
        self.bridge = bridge or SkillBridge(skills)
        self.control = control or RunControl()
        self.on_progress = on_progress
        self.max_steps = max_steps
        self.max_concurrency = max_concurrency
        self.system_prompt = system_prompt
        self.doom = doom or DoomLoopTracker()
        self.detail_level = detail_level
        self.detail_max_chars = detail_max_chars
        self.workdir = workdir
        self.step_extend = step_extend
        self.on_bind_messages = on_bind_messages
        self.on_step = on_step
        self.memory_service = memory_service
        self.plan_reject_feedback = plan_reject_feedback or ""
        self.on_enter_plan = on_enter_plan
        self.mcp_manager = mcp_manager
        self.session_id = session_id or ""
        self.include_run_subagent = include_run_subagent
        if mcp_manager is not None and hasattr(self.bridge, "set_mcp_manager"):
            self.bridge.set_mcp_manager(mcp_manager)
        if hasattr(self.bridge, "bind_subagent_context"):
            self.bridge.bind_subagent_context(
                ai=self.ai,
                skills=self.skills,
                permission=self.permission,
                workdir=self.workdir,
                session_id=self.session_id,
                on_progress=self.on_progress,
                mcp_manager=self.mcp_manager,
            )

    def submit(
        self,
        task: str,
        *,
        history: list[AIMessage] | None = None,
        mode: AgentMode = AgentMode.AGENT,
        system_prompt: str | None = None,
        messages: list[AIMessage] | None = None,
    ) -> AgentResult:
        prompt = system_prompt or self._format_prompt(mode)
        mem_ctx = ""
        if self.memory_service is not None:
            try:
                mem_ctx = self.memory_service.context_for(task) or ""
            except Exception:  # noqa: BLE001
                mem_ctx = ""
        if messages is not None:
            msgs = list(messages)
            if msgs and msgs[0].role == "system":
                msgs[0] = AIMessage(role="system", content=prompt)
            else:
                msgs.insert(0, AIMessage(role="system", content=prompt))
        else:
            msgs = [AIMessage(role="system", content=prompt)]
            if history:
                msgs.extend(
                    AIMessage(
                        role=m.role,
                        content=m.content,
                        name=m.name,
                        tool_call_id=m.tool_call_id,
                        tool_calls=list(m.tool_calls),
                    )
                    for m in history
                    if m.role in {"user", "assistant", "tool"}
                )
            if mem_ctx:
                msgs.append(
                    AIMessage(
                        role="user",
                        content=f"[memory_context]\n{mem_ctx}",
                    )
                )
            msgs.append(AIMessage(role="user", content=task))

        state = LoopState(
            messages=msgs,
            mode=mode,
            max_turns=self.max_steps,
        )
        if self.on_bind_messages is not None:
            self.on_bind_messages(state.messages)

        self.doom.reset()
        self.permission.set_plan_active(mode is AgentMode.PLAN)
        self.control.begin_run()
        try:
            if self.on_progress:
                self.on_progress(
                    {
                        "type": "status",
                        "phase": "started",
                        "message": f"开始（mode={mode.value}）",
                    }
                )
            logger.notice(f"Agent 开始：mode={mode.value} max_turns={self.max_steps}")
            mcp_tools = (
                self.mcp_manager.list_tools() if self.mcp_manager is not None else None
            )
            schemas = build_schemas_for_mode(
                self.skills,
                self.permission,
                mode,
                mcp_tools=mcp_tools,
                include_run_subagent=self.include_run_subagent,
            )
            outcome = query_loop(
                state,
                ai=self.ai,
                skills=self.skills,
                bridge=self.bridge,
                permission=self.permission,
                control=self.control,
                tools_schema=schemas,
                doom=self.doom,
                on_progress=self.on_progress,
                on_step=self.on_step,
                on_enter_plan=self.on_enter_plan,
                max_concurrency=self.max_concurrency,
                step_extend=self.step_extend,
                mcp_tools=mcp_tools,
                include_run_subagent=self.include_run_subagent,
            )
            result = state_to_agent_result(
                outcome.state,
                detail_level=self.detail_level,
                detail_max_chars=self.detail_max_chars,
            )
            if self.on_progress and result.stop_reason == "cancelled":
                self.on_progress({"type": "cancelled", "message": result.answer})
            return result
        finally:
            self.control.end_run()
            self.permission.set_plan_active(False)
            if self.on_bind_messages is not None:
                self.on_bind_messages(None)

    def _format_prompt(self, mode: AgentMode) -> str:
        base = self.system_prompt.strip()
        if mode is AgentMode.PLAN:
            from plan.prompts import plan_mode_prompt

            base = f"{base}\n\n{plan_mode_prompt(reject_feedback=self.plan_reject_feedback)}"
        if self.memory_service is not None:
            try:
                section = self.memory_service.system_section()
                if section:
                    base = f"{base}\n\n{section}"
            except Exception:  # noqa: BLE001
                pass
        wd = self.workdir or Path.cwd()
        return (
            f"{base}\n\n"
            f"当前模式: {mode.value}\n"
            f"当前工作目录: {wd}\n"
            f"当前权限角色: {self.permission.role}\n"
            f"请使用 tools 调用能力；结束时调用 finish"
            f"{' 或 submit_plan' if mode is AgentMode.PLAN else ''}。"
        )
