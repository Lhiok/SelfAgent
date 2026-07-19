"""Plan 生命周期：enter → submit → confirm/reject。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from log import get_logger
from plan.store import load_draft, plan_hash, resolve_plans_dir, save_draft
from plan.types import PlanConfig, PlanPhase, PlanSession
from session.mode import AgentMode
from plan.model import Plan
from workflow.runner import plan_to_workflow_run
from workflow.types import WorkflowRun

logger = get_logger("plan.lifecycle")


class PlanLifecycle:
    def __init__(
        self,
        *,
        config: PlanConfig | None = None,
        plans_base: str | Path | None = None,
    ) -> None:
        self.config = config or _load_config()
        self.plans_base = Path(
            plans_base or self.config.plans_dir
        ).expanduser()
        self.session = PlanSession()

    @property
    def phase(self) -> PlanPhase:
        return self.session.phase

    @property
    def pending_plan(self) -> Plan | None:
        return self.session.plan

    def enter(self, pre_mode: str | AgentMode = AgentMode.AGENT) -> PlanSession:
        mode = (
            pre_mode.value
            if isinstance(pre_mode, AgentMode)
            else str(pre_mode or "agent")
        )
        if mode == AgentMode.PLAN.value:
            mode = AgentMode.AGENT.value
        self.session.pre_mode = mode
        self.session.phase = PlanPhase.PLANNING
        self.session.last_reject_feedback = ""
        # 进入规划时保留旧 draft（可覆盖），不清 plan
        logger.notice(f"plan enter: pre_mode={mode}")
        return self.session

    def submit(self, plan: Plan, *, plan_id: str | None = None) -> PlanSession:
        if not plan.ok:
            raise ValueError("计划无可执行步骤，无法提交确认")
        pid = plan_id or self.session.plan_id or None
        pid, h, path = save_draft(plan, self.plans_base, plan_id=pid)
        self.session.plan = plan
        self.session.plan_id = pid
        self.session.plan_hash = h
        self.session.phase = PlanPhase.AWAITING_CONFIRM
        logger.notice(f"plan submit: id={pid} hash={h} path={path}")
        return self.session

    def confirm(
        self,
        *,
        expected_hash: str | None = None,
        plan: Plan | None = None,
    ) -> WorkflowRun:
        """校验 hash，产出唯一 WorkflowRun；phase → executing。"""
        target = plan or self.session.plan
        if target is None or not target.ok:
            raise ValueError("没有可确认的计划")
        h = plan_hash(target)
        expected = expected_hash if expected_hash is not None else self.session.plan_hash
        if expected and expected != h:
            raise ValueError(f"plan hash 不匹配: expected={expected} actual={h}")

        pid = self.session.plan_id
        if not pid:
            pid, h, _ = save_draft(target, self.plans_base)
            self.session.plan_id = pid
            self.session.plan_hash = h

        run = plan_to_workflow_run(target, name=f"plan:{pid}")
        run.plan_id = pid
        run.plan_hash = h
        self.session.phase = PlanPhase.EXECUTING
        self.session.workflow_run_id = run.id
        self.session.plan = target
        self.session.plan_hash = h
        logger.notice(f"plan confirm: run={run.id}")
        return run

    def finish_execution(self, *, success: bool) -> str:
        """执行结束后清理/恢复。返回应恢复的 mode。"""
        restore = (
            self.session.pre_mode
            if self.config.auto_restore_mode
            else AgentMode.AGENT.value
        )
        if success:
            self.session.plan = None
            self.session.plan_id = ""
            self.session.plan_hash = ""
            self.session.phase = PlanPhase.IDLE
            self.session.last_reject_feedback = ""
        else:
            # 失败保留 draft，回到 awaiting
            self.session.phase = PlanPhase.AWAITING_CONFIRM
        logger.notice(f"plan finish_execution success={success} restore={restore}")
        return restore

    def reject(self, feedback: str = "") -> PlanSession:
        if self.session.phase not in {
            PlanPhase.AWAITING_CONFIRM,
            PlanPhase.PLANNING,
        }:
            if self.session.plan is None:
                raise ValueError("当前没有可拒绝的计划")
        fb = (feedback or "").strip()
        self.session.last_reject_feedback = fb
        self.session.phase = PlanPhase.PLANNING
        # 保留 plan 供修改参考，但需重新 submit
        logger.notice(f"plan reject: feedback={fb[:80]!r}")
        return self.session

    def cancel(self) -> None:
        self.session = PlanSession(pre_mode=self.session.pre_mode)
        self.session.phase = PlanPhase.IDLE

    def restore_awaiting_plan(
        self,
        plan: Plan | None,
        *,
        plan_id: str = "",
        plan_hash_value: str = "",
        pre_mode: str = "agent",
    ) -> None:
        if plan is None or not plan.ok:
            return
        self.session.plan = plan
        self.session.plan_id = plan_id
        self.session.plan_hash = plan_hash_value or plan_hash(plan)
        self.session.pre_mode = pre_mode or "agent"
        self.session.phase = PlanPhase.AWAITING_CONFIRM

    def to_dict(self) -> dict[str, Any]:
        return self.session.to_dict()

    def load_dict(self, data: dict[str, Any] | None) -> None:
        self.session = PlanSession.from_dict(data)


def confirm_plan(
    plan: Plan,
    *,
    base: str | Path,
    expected_hash: str | None = None,
    plan_id: str | None = None,
) -> WorkflowRun:
    lc = PlanLifecycle(plans_base=base)
    if plan_id:
        lc.session.plan_id = plan_id
    return lc.confirm(expected_hash=expected_hash, plan=plan)


def _load_config() -> PlanConfig:
    import config as cfg

    plan_cfg = dict(cfg.get_section("plan", {}) or {})
    if "plans_dir" not in plan_cfg:
        wf = cfg.get_section("workflow", {}) or {}
        if wf.get("plans_dir"):
            plan_cfg["plans_dir"] = wf["plans_dir"]
    return PlanConfig.from_dict(plan_cfg)
