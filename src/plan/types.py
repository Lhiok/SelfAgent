"""Plan Mode 生命周期类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from plan.model import Plan


class PlanPhase(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    AWAITING_CONFIRM = "awaiting_confirm"
    EXECUTING = "executing"


@dataclass
class PlanConfig:
    enabled: bool = True
    plans_dir: str = ".selfagent/plans"
    clear_context_on_confirm: bool = False
    auto_restore_mode: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlanConfig":
        d = dict(data or {})
        return cls(
            enabled=bool(d.get("enabled", True)),
            plans_dir=str(d.get("plans_dir") or ".selfagent/plans"),
            clear_context_on_confirm=bool(d.get("clear_context_on_confirm", False)),
            auto_restore_mode=bool(d.get("auto_restore_mode", True)),
        )


@dataclass
class PlanSession:
    phase: PlanPhase = PlanPhase.IDLE
    plan: Plan | None = None
    plan_id: str = ""
    plan_hash: str = ""
    pre_mode: str = "agent"
    last_reject_feedback: str = ""
    workflow_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "plan_id": self.plan_id,
            "hash": self.plan_hash,
            "pre_mode": self.pre_mode,
            "last_reject_feedback": self.last_reject_feedback,
            "workflow_run_id": self.workflow_run_id,
            "plan": self.plan.to_dict() if self.plan is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlanSession":
        if not data:
            return cls()
        from plan.model import PlanStep

        plan = None
        plan_data = data.get("plan")
        if isinstance(plan_data, dict) and plan_data.get("steps"):
            plan = Plan(
                summary=str(plan_data.get("summary") or ""),
                thought=str(plan_data.get("thought") or ""),
                steps=[
                    PlanStep(
                        index=int(s.get("index") or i + 1),
                        skill=str(s.get("skill") or ""),
                        arguments=dict(s.get("arguments") or {})
                        if isinstance(s.get("arguments"), dict)
                        else {},
                        why=str(s.get("why") or ""),
                        raw_input=str(s.get("raw_input") or ""),
                    )
                    for i, s in enumerate(plan_data.get("steps") or [])
                    if isinstance(s, dict)
                ],
                raw_text=str(plan_data.get("raw_text") or ""),
            )
        phase_raw = str(data.get("phase") or PlanPhase.IDLE.value)
        try:
            phase = PlanPhase(phase_raw)
        except ValueError:
            phase = PlanPhase.AWAITING_CONFIRM if plan and plan.ok else PlanPhase.IDLE
        return cls(
            phase=phase,
            plan=plan,
            plan_id=str(data.get("plan_id") or ""),
            plan_hash=str(data.get("hash") or data.get("plan_hash") or ""),
            pre_mode=str(data.get("pre_mode") or "agent"),
            last_reject_feedback=str(data.get("last_reject_feedback") or ""),
            workflow_run_id=str(data.get("workflow_run_id") or ""),
        )
