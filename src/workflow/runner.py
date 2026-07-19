"""工作流执行器：DAG/线性步骤 → SkillBridge / AgentEngine。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from plan.model import Plan
from skills.bridge import SkillBridge
from workflow.queue import get_workflow_queue
from workflow.run import new_run_id, save_run
from workflow.types import (
    JobPriority,
    QueueMode,
    RunStatus,
    StepStatus,
    WorkflowDef,
    WorkflowRun,
    WorkflowStep,
)

logger = get_logger("workflow.runner")

ProgressFn = Callable[[dict[str, Any]], None]


def plan_to_workflow_run(plan: Plan, *, name: str = "plan") -> WorkflowRun:
    steps: list[WorkflowStep] = []
    for ps in plan.steps:
        steps.append(
            WorkflowStep(
                id=f"s{ps.index}",
                skill=ps.skill,
                input=dict(ps.arguments or {}),
                type="skill",
                why=ps.why,
                depends_on=[f"s{ps.index - 1}"] if ps.index > 1 else [],
            )
        )
    return WorkflowRun(
        id=new_run_id(),
        name=name,
        steps=steps,
        summary=plan.summary,
        status=RunStatus.PENDING,
    )


class WorkflowRunner:
    def __init__(
        self,
        bridge: SkillBridge,
        permission: PermissionGuard,
        *,
        control: RunControl | None = None,
        on_progress: ProgressFn | None = None,
        runs_base: str | Path | None = None,
        agent_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.bridge = bridge
        self.permission = permission
        self.control = control
        self.on_progress = on_progress
        self.runs_base = Path(runs_base or ".selfagent/workflows")
        self.agent_factory = agent_factory

    def from_plan(self, plan: Plan, *, name: str = "plan") -> WorkflowRun:
        return plan_to_workflow_run(plan, name=name)

    def from_def(self, wdef: WorkflowDef) -> WorkflowRun:
        return WorkflowRun(
            id=new_run_id(),
            name=wdef.name,
            mode=wdef.mode,
            on_error=wdef.on_error,
            steps=[
                WorkflowStep(
                    id=s.id,
                    skill=s.skill,
                    input=dict(s.input),
                    type=s.type,
                    prompt=s.prompt,
                    depends_on=list(s.depends_on),
                    why=s.why,
                )
                for s in wdef.steps
            ],
            status=RunStatus.PENDING,
        )

    def run_sync(self, run: WorkflowRun) -> WorkflowRun:
        order = [s.id for s in run.steps]
        by_id = {s.id: s for s in run.steps}
        run.status = RunStatus.RUNNING
        save_run(run, self.runs_base)
        remaining = set(order)

        while remaining:
            if self.control is not None and self.control.cancel_requested:
                run.status = RunStatus.CANCELLED
                for sid in remaining:
                    by_id[sid].status = StepStatus.CANCELLED
                break

            ready = [
                sid
                for sid in order
                if sid in remaining
                and all(
                    by_id[d].status == StepStatus.DONE
                    for d in by_id[sid].depends_on
                    if d in by_id
                )
            ]
            if not ready:
                for sid in remaining:
                    by_id[sid].status = StepStatus.FAILED
                    by_id[sid].output = "依赖未满足或死锁"
                    by_id[sid].ok = False
                run.status = RunStatus.FAILED
                break

            failed_stop = False
            for sid in ready:
                step = by_id[sid]
                self._emit(
                    {
                        "type": "status",
                        "phase": "workflow",
                        "message": f"执行步骤 {sid}",
                        "step": sid,
                    }
                )
                step.status = StepStatus.RUNNING
                ok, output = self._execute_step(step, run)
                step.ok = ok
                step.output = output
                step.status = StepStatus.DONE if ok else StepStatus.FAILED
                remaining.discard(sid)
                run.steps = [by_id[i] for i in order]
                save_run(run, self.runs_base)
                if not ok and run.on_error == "stop":
                    for other in list(remaining):
                        by_id[other].status = StepStatus.SKIPPED
                    remaining.clear()
                    run.status = RunStatus.FAILED
                    failed_stop = True
                    break
            if failed_stop:
                break
        else:
            if all(by_id[i].status == StepStatus.DONE for i in order):
                run.status = RunStatus.DONE
            elif any(by_id[i].status == StepStatus.FAILED for i in order):
                run.status = RunStatus.FAILED
            elif run.status != RunStatus.CANCELLED:
                run.status = RunStatus.DONE

        run.steps = [by_id[i] for i in order]
        save_run(run, self.runs_base)
        get_workflow_queue().push(
            f"[workflow] {run.name or run.id} → {run.status.value}",
            priority=JobPriority.NEXT,
            mode=QueueMode.NOTIFICATION,
            source="workflow.runner",
            meta={"run_id": run.id, "status": run.status.value},
        )
        logger.notice(f"WorkflowRun {run.id} {run.status.value}")
        return run

    def _execute_step(self, step: WorkflowStep, run: WorkflowRun) -> tuple[bool, str]:
        if step.type == "agent" or (not step.skill and step.prompt):
            return self._run_agent_step(step, run)
        if not step.skill:
            return False, "步骤缺少 skill"
        result = self.bridge.invoke(
            step.skill,
            step.input,
            permission=self.permission,
            control=self.control,
            on_progress=self.on_progress,
        )
        text = result.output if result.ok else f"ERROR: {result.output}"
        return result.ok, text

    def _run_agent_step(self, step: WorkflowStep, run: WorkflowRun) -> tuple[bool, str]:
        if self.agent_factory is None:
            return False, "未配置 agent_factory，无法执行 agent 步骤"
        prev = "\n\n".join(
            f"[{s.id}] {s.output}"
            for s in run.steps
            if s.status == StepStatus.DONE and s.output
        )
        prompt = step.prompt or "继续完成任务"
        if prev:
            prompt = f"前序步骤输出:\n{prev}\n\n任务:\n{prompt}"
        try:
            agent = self.agent_factory()
            result = agent.run(prompt)
            return bool(result.completed), result.answer or ""
        except Exception as exc:  # noqa: BLE001
            return False, f"agent 步骤失败: {exc}"

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(event)
        except Exception:  # noqa: BLE001
            pass
