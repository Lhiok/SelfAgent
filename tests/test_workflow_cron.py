"""workflow.cron 调度触发入队（假时钟）。"""

from __future__ import annotations

from pathlib import Path

from workflow.cron import CronScheduler
from workflow.queue import get_workflow_queue, reset_workflow_queue
from workflow.types import QueueMode


def test_tick_fires_with_fake_clock(tmp_path: Path) -> None:
    reset_workflow_queue()
    clock = {"t": 1000.0}

    def now() -> float:
        return clock["t"]

    fired: list[str] = []
    sched = CronScheduler(
        tmp_path / "scheduled_tasks.json",
        clock=now,
        on_fire=lambda task: fired.append(task.prompt),
    )
    task = sched.add("hello cron", every_sec=10)
    assert task.next_fire_at == 1010.0

    assert sched.tick() == []
    clock["t"] = 1009.0
    assert sched.tick() == []

    clock["t"] = 1010.0
    out = sched.tick()
    assert len(out) == 1
    assert fired == ["hello cron"]
    assert out[0].next_fire_at == 1020.0


def test_default_on_fire_enqueues(tmp_path: Path) -> None:
    reset_workflow_queue()
    clock = {"t": 50.0}
    sched = CronScheduler(
        tmp_path / "scheduled_tasks.json",
        clock=lambda: clock["t"],
    )
    sched.add("ping", every_sec=5)
    clock["t"] = 55.0
    sched.tick()
    items = get_workflow_queue().drain(max_items=5, modes={QueueMode.CRON})
    assert [i.text for i in items] == ["ping"]
