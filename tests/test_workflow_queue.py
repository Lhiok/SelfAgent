"""workflow.queue 优先级与 drain。"""

from __future__ import annotations

from workflow.queue import WorkflowQueue, reset_workflow_queue
from workflow.types import JobPriority, QueueMode


def setup_function() -> None:
    reset_workflow_queue()


def test_priority_order() -> None:
    q = WorkflowQueue()
    q.push("later", priority=JobPriority.LATER)
    q.push("next", priority=JobPriority.NEXT)
    q.push("now", priority=JobPriority.NOW)
    items = q.drain(max_items=3, max_priority=JobPriority.LATER)
    assert [i.text for i in items] == ["now", "next", "later"]


def test_drain_respects_max_priority() -> None:
    q = WorkflowQueue()
    q.push("now", priority=JobPriority.NOW)
    q.push("later", priority=JobPriority.LATER)
    items = q.drain(max_items=10, max_priority=JobPriority.NEXT)
    assert [i.text for i in items] == ["now"]
    assert q.size() == 1


def test_drain_filters_modes() -> None:
    q = WorkflowQueue()
    q.push("p", mode=QueueMode.PROMPT)
    q.push("n", mode=QueueMode.NOTIFICATION)
    items = q.drain(max_items=10, modes={QueueMode.NOTIFICATION})
    assert [i.text for i in items] == ["n"]
    assert q.size() == 1


def test_run_control_bridges_queue() -> None:
    from session.control import RunControl
    from workflow.queue import get_workflow_queue

    reset_workflow_queue()
    get_workflow_queue().push(
        "from-wf", priority=JobPriority.NEXT, mode=QueueMode.NOTIFICATION
    )
    ctl = RunControl()
    ctl.enqueue("local")
    drained = ctl.drain_pending()
    assert "local" in drained
    assert "from-wf" in drained
