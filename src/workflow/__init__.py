"""工作流编排层：队列 / DAG / YAML / cron / Worker（叠在 AgentEngine 之上）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from workflow.cron import CronScheduler
from workflow.loader import load_workflows
from plan.store import load_draft as load_plan, plan_hash, save_draft as save_plan
from workflow.queue import WorkflowQueue, get_workflow_queue, reset_workflow_queue
from workflow.registry import CommandRegistry, SlashCommand
from workflow.run import list_runs, load_run, save_run
from workflow.runner import WorkflowRunner, plan_to_workflow_run
from workflow.tasks import TaskStore
from workflow.types import (
    JobPriority,
    QueueItem,
    QueueMode,
    RunStatus,
    StepStatus,
    WorkflowDef,
    WorkflowRun,
    WorkflowStep,
)
from workflow.workers import WorkerPool

if TYPE_CHECKING:
    from permission import PermissionGuard
    from session.control import RunControl
    from skills.bridge import SkillBridge


class WorkflowEngine:
    """门面：加载 YAML、跑工作流、任务库、可选 cron/workers。"""

    def __init__(
        self,
        bridge: "SkillBridge",
        permission: "PermissionGuard",
        *,
        control: "RunControl | None" = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        agent_factory: Callable[[], Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        import config as cfg
        from log import get_logger
        from pathlib import Path

        logger = get_logger("workflow")
        wf = dict(config or cfg.get_section("workflow", {}) or {})
        self.enabled = bool(wf.get("enabled", True))
        self.max_workers = int(wf.get("max_workers", 2))
        self.workflows_dir = Path(wf.get("workflows_dir", ".selfagent/workflows"))
        self.plans_dir = Path(wf.get("plans_dir", ".selfagent/plans"))
        self.tasks_dir = Path(wf.get("tasks_dir", ".selfagent/tasks"))
        self.cron_enabled = bool(wf.get("cron_enabled", False))
        self.bridge = bridge
        self.permission = permission
        self.control = control
        self.on_progress = on_progress
        self.agent_factory = agent_factory
        self.queue = get_workflow_queue()
        self.registry = CommandRegistry()
        self.task_list_id = str(wf.get("task_list_id") or "default")
        self.tasks = TaskStore(self.tasks_dir, self.task_list_id)
        self.runner = WorkflowRunner(
            bridge,
            permission,
            control=control,
            on_progress=on_progress,
            runs_base=self.workflows_dir,
            agent_factory=agent_factory,
        )
        self.cron = CronScheduler(
            Path(".selfagent/scheduled_tasks.json"),
            poll_interval=float(wf.get("cron_poll_interval", 1.0)),
        )
        self.workers: WorkerPool | None = None
        if agent_factory is not None:
            self.workers = WorkerPool(
                self.tasks,
                agent_factory,
                max_workers=self.max_workers,
            )
        n = self.reload_workflows()
        logger.notice(f"WorkflowEngine ready, workflows={n}")

    def reload_workflows(self) -> int:
        return self.registry.load_workflows_dir(str(self.workflows_dir))

    def start_background(self) -> None:
        if self.cron_enabled:
            self.cron.start()

    def stop_background(self) -> None:
        self.cron.stop()
        if self.workers is not None:
            self.workers.shutdown(wait=False)

    def run_def(self, name: str) -> WorkflowRun:
        cmd = self.registry.get(name)
        if cmd is None or cmd.workflow is None:
            raise KeyError(f"未知工作流: {name}")
        run = self.runner.from_def(cmd.workflow)
        return self.runner.run_sync(run)

    def run_workflow_run(self, run: WorkflowRun) -> WorkflowRun:
        return self.runner.run_sync(run)

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return load_run(run_id, self.workflows_dir)

    def list_tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.tasks.list_tasks()]


def confirm_plan(*args: Any, **kwargs: Any):
    from plan.lifecycle import confirm_plan as _confirm_plan

    return _confirm_plan(*args, **kwargs)


__all__ = [
    "WorkflowEngine",
    "WorkflowQueue",
    "WorkflowRunner",
    "WorkflowRun",
    "WorkflowStep",
    "WorkflowDef",
    "JobPriority",
    "QueueMode",
    "QueueItem",
    "RunStatus",
    "StepStatus",
    "TaskStore",
    "CommandRegistry",
    "SlashCommand",
    "CronScheduler",
    "WorkerPool",
    "get_workflow_queue",
    "reset_workflow_queue",
    "load_workflows",
    "plan_to_workflow_run",
    "confirm_plan",
    "save_plan",
    "load_plan",
    "plan_hash",
    "save_run",
    "load_run",
    "list_runs",
]
