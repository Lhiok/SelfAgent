"""工具执行：permission → SkillBridge；安全工具可并行。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from ai import AIMessage, ToolCall
from agent.tools import (
    ENTER_PLAN_MODE_TOOL,
    FINISH_TOOL,
    SUBMIT_PLAN_TOOL,
    is_concurrency_safe,
)
from agent.types import ToolResultMsg
from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from plan.model import Plan, PlanStep
from skills.base import SkillResult
from skills.bridge import SkillBridge
from skills.ask_user import coerce_questions

logger = get_logger("agent.tool_runner")

ProgressFn = Callable[[dict[str, Any]], None]
EnterPlanFn = Callable[[str], None]


def parse_args(raw: str | None) -> dict[str, Any]:
    text = (raw or "").strip() or "{}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    return data if isinstance(data, dict) else {"value": data}


@dataclass
class RunBatchResult:
    tool_results: list[ToolResultMsg] = field(default_factory=list)
    user_attachments: list[AIMessage] = field(default_factory=list)
    plan: Plan | None = None
    finish_answer: str | None = None
    entered_plan: bool = False
    enter_plan_reason: str = ""


class ToolRunner:
    def __init__(
        self,
        bridge: SkillBridge,
        permission: PermissionGuard,
        *,
        control: RunControl | None = None,
        on_progress: ProgressFn | None = None,
        on_enter_plan: EnterPlanFn | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.bridge = bridge
        self.permission = permission
        self.control = control
        self.on_progress = on_progress
        self.on_enter_plan = on_enter_plan
        self.max_concurrency = max(1, int(max_concurrency))
        self.ask_buffer: list[dict[str, Any]] = []
        self._ask_seq = 0

    def run_calls(self, calls: list[ToolCall]) -> RunBatchResult:
        out = RunBatchResult()
        if not calls:
            return out

        skill_calls: list[ToolCall] = []
        need_flush = False

        for tc in calls:
            name = (tc.name or "").strip()
            if name == FINISH_TOOL:
                need_flush = True
                args = parse_args(tc.arguments)
                out.finish_answer = str(args.get("answer") or "").strip()
                out.tool_results.append(
                    ToolResultMsg(
                        tool_call_id=tc.id,
                        name=name,
                        content=out.finish_answer or "(empty)",
                        ok=True,
                    )
                )
                continue
            if name == SUBMIT_PLAN_TOOL:
                need_flush = True
                out.plan = self._parse_plan(tc)
                out.tool_results.append(
                    ToolResultMsg(
                        tool_call_id=tc.id,
                        name=name,
                        content=(
                            out.plan.format_text()
                            if out.plan.ok
                            else (out.plan.summary or "empty plan")
                        ),
                        ok=out.plan.ok or bool(out.plan.summary),
                        data=out.plan.to_dict(),
                    )
                )
                continue
            if name == ENTER_PLAN_MODE_TOOL:
                args = parse_args(tc.arguments)
                reason = str(args.get("reason") or "").strip()
                out.entered_plan = True
                out.enter_plan_reason = reason
                if self.on_enter_plan is not None:
                    try:
                        self.on_enter_plan(reason)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"on_enter_plan 失败: {exc}")
                        out.tool_results.append(
                            ToolResultMsg(
                                tool_call_id=tc.id,
                                name=name,
                                content=f"ERROR: 进入 Plan Mode 失败: {exc}",
                                ok=False,
                            )
                        )
                        out.entered_plan = False
                        continue
                out.tool_results.append(
                    ToolResultMsg(
                        tool_call_id=tc.id,
                        name=name,
                        content=(
                            "已进入 Plan Mode（只读）。"
                            "请调研后调用 submit_plan 提交计划。"
                            + (f" 原因: {reason}" if reason else "")
                        ),
                        ok=True,
                        data={"entered_plan": True, "reason": reason},
                    )
                )
                continue
            if name == "ask_user":
                args = parse_args(tc.arguments)
                if args.get("commit") or args.get("flush"):
                    skill_calls.append(tc)
                    need_flush = True
                else:
                    items = coerce_questions(args)
                    if not items:
                        out.tool_results.append(
                            ToolResultMsg(
                                tool_call_id=tc.id,
                                name=name,
                                content="ERROR: ask_user 需要 question+options，或非空 questions 数组",
                                ok=False,
                            )
                        )
                    else:
                        for item in items:
                            self._ask_seq += 1
                            buffered = dict(item)
                            buffered["id"] = str(self._ask_seq)
                            self.ask_buffer.append(buffered)
                        total = len(self.ask_buffer)
                        out.tool_results.append(
                            ToolResultMsg(
                                tool_call_id=tc.id,
                                name=name,
                                content=(
                                    f"已暂存 {len(items)} 个问题（合计 {total} 个，尚未询问用户）。"
                                    "将在 finish/submit_plan/其它工具前一并询问用户。"
                                ),
                                ok=True,
                                data={"buffered": True, "total": total, "added": len(items)},
                            )
                        )
                continue
            skill_calls.append(tc)
            need_flush = True

        if need_flush and self.ask_buffer:
            attach = self._flush_ask_as_user()
            if attach is not None:
                out.user_attachments.append(attach)

        if skill_calls:
            out.tool_results.extend(self._run_skill_calls(skill_calls))

        return out

    def synthetic_cancel_results(self, calls: list[ToolCall]) -> list[ToolResultMsg]:
        return [
            ToolResultMsg(
                tool_call_id=tc.id,
                name=tc.name,
                content="已取消",
                ok=False,
                data={"cancelled": True},
            )
            for tc in calls
        ]

    def flush_pending_asks(self) -> tuple["ActionCall | None", AIMessage | None]:
        """将暂存 ask_user 一次性询问用户，供纯文本收尾等场景使用。"""
        from agent.types import ActionCall

        if not self.ask_buffer:
            return None, None
        items = list(self.ask_buffer)
        self.ask_buffer.clear()
        if self.on_progress:
            try:
                self.on_progress(
                    {
                        "type": "status",
                        "phase": "ask_buffer",
                        "message": f"提交 {len(items)} 个暂存问题…",
                    }
                )
            except Exception:  # noqa: BLE001
                pass
        result = self.bridge.invoke(
            "ask_user",
            {"questions": items},
            permission=self.permission,
            control=self.control,
            on_progress=self.on_progress,
        )
        text = result.output if result.ok else f"ERROR: {result.output}"
        action = ActionCall(
            action="ask_user",
            action_input='{"commit": true}',
            observation=text,
            ok=result.ok,
            data=dict(result.data or {}),
        )
        attach = AIMessage(role="user", content=f"用户对暂存问题的回答:\n{text}")
        return action, attach

    def _flush_ask_as_user(self) -> AIMessage | None:
        _, attach = self.flush_pending_asks()
        return attach

    def _run_skill_calls(self, calls: list[ToolCall]) -> list[ToolResultMsg]:
        from agent.tool_orchestration import partition_tool_calls

        batches = partition_tool_calls(
            calls,
            is_safe=is_concurrency_safe,
            parse_args=parse_args,
            max_concurrency=self.max_concurrency,
        )

        out: list[ToolResultMsg] = []
        for batch in batches:
            if len(batch) == 1 or not is_concurrency_safe(
                batch[0].name, parse_args(batch[0].arguments)
            ):
                for tc in batch:
                    out.append(self._invoke_one(tc))
            else:
                out.extend(self._invoke_parallel(batch))
        return out

    def _invoke_parallel(self, batch: list[ToolCall]) -> list[ToolResultMsg]:
        results: dict[str, ToolResultMsg] = {}
        with ThreadPoolExecutor(max_workers=min(len(batch), self.max_concurrency)) as pool:
            futs = {pool.submit(self._invoke_one, tc): tc for tc in batch}
            for fut in as_completed(futs):
                tc = futs[fut]
                try:
                    results[tc.id] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    results[tc.id] = ToolResultMsg(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=f"ERROR: {exc}",
                        ok=False,
                    )
        return [results[tc.id] for tc in batch]

    def _invoke_one(self, tc: ToolCall) -> ToolResultMsg:
        if self.control is not None and self.control.cancel_requested:
            return ToolResultMsg(
                tool_call_id=tc.id,
                name=tc.name,
                content="已取消",
                ok=False,
                data={"cancelled": True},
            )
        args = parse_args(tc.arguments)
        result: SkillResult = self.bridge.invoke(
            tc.name,
            args,
            permission=self.permission,
            control=self.control,
            on_progress=self.on_progress,
        )
        text = result.output if result.ok else f"ERROR: {result.output}"
        return ToolResultMsg(
            tool_call_id=tc.id,
            name=tc.name,
            content=text,
            ok=result.ok,
            data=dict(result.data or {}),
        )

    def _parse_plan(self, tc: ToolCall) -> Plan:
        args = parse_args(tc.arguments)
        steps_raw = args.get("steps") or []
        steps: list[PlanStep] = []
        for i, item in enumerate(steps_raw, start=1):
            if not isinstance(item, dict):
                continue
            skill = str(item.get("skill") or "").strip()
            if not skill:
                continue
            inp = item.get("input") if isinstance(item.get("input"), dict) else {}
            why = str(item.get("why") or "")
            steps.append(
                PlanStep(
                    index=i,
                    skill=skill,
                    arguments=dict(inp or {}),
                    why=why,
                    raw_input=json.dumps(inp or {}, ensure_ascii=False),
                )
            )
        return Plan(
            summary=str(args.get("summary") or ""),
            thought=str(args.get("thought") or ""),
            steps=steps,
            raw_text=tc.arguments or "",
        )
