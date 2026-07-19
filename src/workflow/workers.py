"""轻量 Worker 池：认领 DAG 任务并跑 AgentEngine。"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from log import get_logger
from workflow.queue import get_workflow_queue
from workflow.tasks import TaskStore
from workflow.types import JobPriority, QueueMode

logger = get_logger("workflow.workers")


class WorkerPool:
    def __init__(
        self,
        tasks: TaskStore,
        agent_factory: Callable[[], Any],
        *,
        max_workers: int = 2,
        log_dir: str | Path = "logs/runs",
    ) -> None:
        self.tasks = tasks
        self.agent_factory = agent_factory
        self.max_workers = max(1, int(max_workers))
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers)
        self._lock = threading.Lock()

    def dispatch_ready(self, *, prompt_template: str = "完成任务: {title}") -> list[Future]:
        ready = self.tasks.ready_tasks()
        futs: list[Future] = []
        for node in ready[: self.max_workers]:
            owner = f"worker-{uuid.uuid4().hex[:8]}"
            claimed = self.tasks.claim(node.id, owner)
            if claimed is None:
                continue
            fut = self._pool.submit(self._run_one, claimed.id, owner, prompt_template)
            futs.append(fut)
        return futs

    def _run_one(self, task_id: str, owner: str, prompt_template: str) -> dict[str, Any]:
        node = self.tasks.get(task_id)
        if node is None:
            return {"ok": False, "error": "missing task"}
        prompt = prompt_template.format(title=node.title, id=node.id)
        log_path = self.log_dir / f"{task_id}.log"
        try:
            agent = self.agent_factory()
            result = agent.run(prompt)
            ok = bool(result.completed)
            output = result.answer or ""
            log_path.write_text(output, encoding="utf-8")
            self.tasks.update(
                task_id,
                status="done" if ok else "failed",
                owner="",
                output=output[:8000],
            )
            get_workflow_queue().push(
                f"[worker] task {task_id} → {'done' if ok else 'failed'}: {output[:200]}",
                priority=JobPriority.NEXT,
                mode=QueueMode.NOTIFICATION,
                source="workflow.workers",
                meta={"task_id": task_id, "ok": ok},
            )
            return {"ok": ok, "task_id": task_id, "output": output}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"worker 失败 {task_id}: {exc}")
            self.tasks.update(
                task_id, status="failed", owner="", output=str(exc)
            )
            get_workflow_queue().push(
                f"[worker] task {task_id} failed: {exc}",
                priority=JobPriority.NEXT,
                mode=QueueMode.NOTIFICATION,
                source="workflow.workers",
            )
            return {"ok": False, "task_id": task_id, "error": str(exc)}

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait)
