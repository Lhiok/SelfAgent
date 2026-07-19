"""workflow.tasks DAG 与 claim。"""

from __future__ import annotations

from pathlib import Path

from workflow.tasks import TaskStore


def test_dag_ready_and_claim(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks", "list1")
    a = store.create("A")
    b = store.create("B", blocked_by=[a.id])
    ready = store.ready_tasks()
    assert {t.id for t in ready} == {a.id}

    claimed = store.claim(a.id, "w1")
    assert claimed is not None
    assert claimed.status == "running"
    assert store.claim(a.id, "w2") is None  # 冲突

    store.update(a.id, status="done", owner="")
    ready2 = store.ready_tasks()
    assert {t.id for t in ready2} == {b.id}
    assert store.claim(b.id, "w1") is not None


def test_claim_blocked_denied(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks", "x")
    a = store.create("A")
    b = store.create("B", blocked_by=[a.id])
    assert store.claim(b.id, "w") is None
