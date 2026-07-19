"""ask_user 取消哨兵唤醒等待队列。"""

from __future__ import annotations

import queue
import threading
import time

from app.store import ASK_CANCEL_SENTINEL, WorkspaceStore


def test_cancel_run_wakes_pending_ask(tmp_path):
    store = WorkspaceStore(root=tmp_path / "store")
    workdir = tmp_path / "project"
    workdir.mkdir()
    ws = store.add_workspace(str(workdir), title="t")
    session = store.create_session(ws["id"])
    sid = session["session_id"]

    answer_q: queue.Queue[str] = queue.Queue(maxsize=1)
    with store._global:
        store._pending_asks[sid] = {
            "ask_id": "ask1",
            "queue": answer_q,
            "event": {"type": "ask_user", "ask_id": "ask1"},
            "orphaned": False,
            "checkpoint": {},
        }

    got: list[str] = []

    def waiter() -> None:
        got.append(answer_q.get(timeout=2))

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    out = store.cancel_run(sid)
    t.join(timeout=2)
    assert out["ok"] is True
    assert got == [ASK_CANCEL_SENTINEL]
