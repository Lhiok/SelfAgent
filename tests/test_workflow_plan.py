"""plan.store 落盘 + lifecycle confirm hash。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plan.lifecycle import PlanLifecycle
from plan.store import load_draft, plan_hash, save_draft
from plan.model import Plan, PlanStep


def _sample_plan() -> Plan:
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


def test_save_and_confirm_hash(tmp_path: Path) -> None:
    plan = _sample_plan()
    pid, h, path = save_draft(plan, tmp_path)
    assert path.is_file()
    loaded = load_draft(pid, tmp_path)
    assert loaded is not None
    p2, h2 = loaded
    assert h2 == h
    assert p2.summary == "demo"

    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter()
    lc.submit(plan, plan_id=pid)
    run = lc.confirm(expected_hash=h)
    assert run.plan_id == pid
    assert run.plan_hash == h
    assert len(run.steps) == 1


def test_confirm_hash_mismatch(tmp_path: Path) -> None:
    plan = _sample_plan()
    lc = PlanLifecycle(plans_base=tmp_path)
    lc.enter()
    lc.submit(plan)
    with pytest.raises(ValueError, match="hash"):
        lc.confirm(expected_hash="deadbeef")


def test_plan_hash_stable() -> None:
    p = _sample_plan()
    assert plan_hash(p) == plan_hash(p)
