"""Session / Agent 公共类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ai import AIMessage
from session.detail import DETAIL_OFF, format_result_detail
from session.mode import AgentMode
from plan.model import Plan

DetailHandler = Callable[[str], None]
# 进度事件：{"type":"status"|"step"|...}，供 CLI/Web 实时展示
ProgressHandler = Callable[[dict[str, Any]], None]

DEFAULT_SYSTEM_PROMPT = """你是可调用工具的助手。请通过 function/tool 调用完成任务。
任务完成时调用 finish，参数 {"answer":"..."}。
可在一轮中发起多个互不依赖的工具调用。
ask_user 可连续调用暂存问题；finish / submit_plan / 其它工具前会一并询问用户。
"""

@dataclass
class ActionCall:
    action: str
    action_input: str
    observation: str | None = None
    ok: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentStep:
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
        return format_observations(self.calls)


@dataclass
class AgentResult:
    answer: str
    steps: list[AgentStep] = field(default_factory=list)
    completed: bool = False
    messages: list[AIMessage] = field(default_factory=list)
    mode: str = AgentMode.AGENT.value
    plan: Plan | None = None
    # detail != off 时填充
    detail_text: str = ""
    detail_level: str = DETAIL_OFF
    # 未完成原因（如 cancelled / doom_loop），供 UI 展示
    stop_reason: str = ""
    # 工作流执行 id（execute_plan / confirm 路径）
    workflow_run_id: str = ""

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
    steps: list[AgentStep] = field(default_factory=list)
    completed: bool = False
    messages: list[AIMessage] = field(default_factory=list)
    detail_text: str = ""
    detail_level: str = DETAIL_OFF

    def to_agent_result(self) -> AgentResult:
        return AgentResult(
            answer=self.answer,
            steps=self.steps,
            completed=self.completed,
            messages=self.messages,
            mode=AgentMode.PLAN.value,
            plan=self.plan,
            detail_text=self.detail_text,
            detail_level=self.detail_level,
        )


def format_observations(calls: list[ActionCall]) -> str:
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


def summarize_plan_execution(steps: list[AgentStep]) -> str:
    """生成简洁的计划执行摘要（同文件多处改动合并，避免重复罗列）。"""
    from session.changes import collect_changes_from_steps

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
