"""plan.lifecycle 状态机。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plan.lifecycle import PlanLifecycle
from plan.types import PlanPhase
from plan.model import Plan, PlanStep


def _plan() -> Plan:
    return Plan(
        summary="demo",
        steps=[
            PlanStep(
                index=1,
                skill="local_file",
                arguments={"action": "list", "path": "."},
                why="list",
            )
        ],
    )


def test_enter_submit_confirm_single_run(tmp_path: Path) -> None:
    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter("agent")
    assert lc.phase is PlanPhase.PLANNING
    lc.submit(_plan())
    assert lc.phase is PlanPhase.AWAITING_CONFIRM
    assert lc.session.plan_hash
    run = lc.confirm()
    assert run.plan_id == lc.session.plan_id
    assert run.plan_hash == lc.session.plan_hash
    assert lc.phase is PlanPhase.EXECUTING
    # 再次 confirm 应仍可基于同一 plan（测试只验证单 run id 稳定字段）
    assert run.id


def test_reject_returns_to_planning(tmp_path: Path) -> None:
    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter("agent")
    lc.submit(_plan())
    lc.reject("请改用 patch 而不是 write")
    assert lc.phase is PlanPhase.PLANNING
    assert "patch" in lc.session.last_reject_feedback


def test_confirm_hash_mismatch(tmp_path: Path) -> None:
    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter()
    lc.submit(_plan())
    with pytest.raises(ValueError, match="hash"):
        lc.confirm(expected_hash="deadbeefdeadbeef")


def test_finish_execution_restores(tmp_path: Path) -> None:
    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter("agent")
    lc.submit(_plan())
    lc.confirm()
    mode = lc.finish_execution(success=True)
    assert mode == "agent"
    assert lc.phase is PlanPhase.IDLE
    assert lc.pending_plan is None
