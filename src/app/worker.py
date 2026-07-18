"""后台消费 WorkspaceStore 流式事件。"""

from __future__ import annotations

from typing import Any, Literal

from PySide6.QtCore import QThread, Signal

from app.store import WorkspaceStore

RunKind = Literal["chat", "confirm"]


class RunThread(QThread):
    event_received = Signal(object)  # dict event
    run_finished = Signal()

    def __init__(
        self,
        store: WorkspaceStore,
        kind: RunKind,
        session_id: str,
        message: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._kind = kind
        self._session_id = session_id
        self._message = message

    def run(self) -> None:
        try:
            if self._kind == "confirm":
                iterator = self._store.iter_confirm_events(self._session_id)
            else:
                iterator = self._store.iter_chat_events(self._session_id, self._message)
            for event in iterator:
                if isinstance(event, dict):
                    self.event_received.emit(event)
        except Exception as exc:  # noqa: BLE001
            self.event_received.emit({"type": "error", "message": str(exc)})
        finally:
            self.run_finished.emit()
