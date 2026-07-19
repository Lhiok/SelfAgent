"""工作流类型（对齐 Claude Code 编排层概念）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobPriority(str, Enum):
    NOW = "now"
    NEXT = "next"
    LATER = "later"

    @property
    def rank(self) -> int:
        return {"now": 0, "next": 1, "later": 2}[self.value]


class QueueMode(str, Enum):
    PROMPT = "prompt"
    NOTIFICATION = "notification"
    CRON = "cron"


class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueueItem:
    text: str
    priority: JobPriority = JobPriority.NEXT
    mode: QueueMode = QueueMode.PROMPT
    source: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStep:
    id: str
    skill: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "skill"  # skill | agent
    prompt: str = ""
    depends_on: list[str] = field(default_factory=list)
    why: str = ""
    status: StepStatus = StepStatus.PENDING
    output: str = ""
    ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill": self.skill,
            "input": dict(self.input),
            "type": self.type,
            "prompt": self.prompt,
            "depends_on": list(self.depends_on),
            "why": self.why,
            "status": self.status.value,
            "output": self.output,
            "ok": self.ok,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowStep":
        return cls(
            id=str(data.get("id") or ""),
            skill=data.get("skill"),
            input=dict(data.get("input") or {})
            if isinstance(data.get("input"), dict)
            else {},
            type=str(data.get("type") or "skill"),
            prompt=str(data.get("prompt") or ""),
            depends_on=[str(x) for x in (data.get("depends_on") or [])],
            why=str(data.get("why") or ""),
            status=StepStatus(str(data.get("status") or "pending")),
            output=str(data.get("output") or ""),
            ok=data.get("ok"),
        )


@dataclass
class WorkflowRun:
    id: str
    name: str = ""
    status: RunStatus = RunStatus.PENDING
    steps: list[WorkflowStep] = field(default_factory=list)
    mode: str = "agent"
    on_error: str = "stop"  # stop | continue
    task_list_id: str = ""
    plan_id: str = ""
    plan_hash: str = ""
    summary: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "mode": self.mode,
            "on_error": self.on_error,
            "task_list_id": self.task_list_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "summary": self.summary,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowRun":
        steps = [
            WorkflowStep.from_dict(s)
            for s in (data.get("steps") or [])
            if isinstance(s, dict)
        ]
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            status=RunStatus(str(data.get("status") or "pending")),
            steps=steps,
            mode=str(data.get("mode") or "agent"),
            on_error=str(data.get("on_error") or "stop"),
            task_list_id=str(data.get("task_list_id") or ""),
            plan_id=str(data.get("plan_id") or ""),
            plan_hash=str(data.get("plan_hash") or ""),
            summary=str(data.get("summary") or ""),
            meta=dict(data.get("meta") or {})
            if isinstance(data.get("meta"), dict)
            else {},
        )


@dataclass
class WorkflowDef:
    name: str
    description: str = ""
    mode: str = "agent"
    on_error: str = "stop"
    steps: list[WorkflowStep] = field(default_factory=list)
    source_path: str = ""
