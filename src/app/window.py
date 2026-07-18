"""SelfAgent 原生桌面工作台（对齐 web 功能）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import config as cfg
from app.ask_dialog import AskDialog
from app.chat_pane import ChatPane
from app.inspector import Inspector
from app.sidebar import Sidebar
from app.store import WorkspaceStore
from app.worker import RunThread
from log import reset_logger


class MainWindow(QMainWindow):
    def __init__(self, *, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("SelfAgent")
        self.resize(1280, 800)

        root = Path(__file__).resolve().parents[2]
        cfg_path = config_path or (root / "config.yaml")
        if cfg_path.is_file():
            cfg.load_config(cfg_path)
        reset_logger()

        self.store = WorkspaceStore()
        self._session_id: str | None = None
        self._session: dict[str, Any] | None = None
        self._busy = False
        self._run: RunThread | None = None
        self._live_steps: list[dict[str, Any]] = []
        self._pending_ask: dict[str, Any] | None = None

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 工具条挂在中间栏标题旁：模式 / 细节 / 删除（整体布局仿 Agents 三栏）
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        self.sidebar = Sidebar()
        self.chat = ChatPane()
        self.inspector = Inspector()

        self.workdir_label = QLabel("")
        self.workdir_label.setObjectName("WorkdirLabel")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["agent", "plan"])
        self.mode_combo.setToolTip("运行模式")
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.detail_combo = QComboBox()
        self.detail_combo.addItems(["off", "summary", "full"])
        self.detail_combo.setToolTip("过程细节")
        self.detail_combo.currentTextChanged.connect(self._on_detail_changed)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setObjectName("DangerButton")
        self.btn_delete.clicked.connect(self._delete_session)
        header = self.chat.findChild(QWidget, "ChatHeader")
        if header is not None:
            hl = header.layout()
            if hl is not None:
                hl.addWidget(self.workdir_label)
                hl.addWidget(self.mode_combo)
                hl.addWidget(self.detail_combo)
                hl.addWidget(self.btn_delete)

        split.addWidget(self.sidebar)
        split.addWidget(self.chat)
        split.addWidget(self.inspector)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setStretchFactor(2, 4)
        split.setSizes([260, 560, 420])
        outer.addWidget(split, 1)
        self.title_label = self.chat.chat_title

        self.sidebar.session_selected.connect(self._select_session)
        self.sidebar.add_workspace_requested.connect(self._add_workspace)
        self.sidebar.new_session_requested.connect(self._new_session)
        self.sidebar.refresh_requested.connect(self._refresh_sidebar)
        self.sidebar.session_patch_requested.connect(self._patch_session)
        self.sidebar.workspace_patch_requested.connect(self._patch_workspace)
        self.sidebar.session_delete_requested.connect(self._delete_session_by_id)

        self.chat.send_requested.connect(self._send)
        self.chat.confirm_plan_requested.connect(self._confirm_plan)
        self.chat.open_ask_requested.connect(self._open_ask)
        self.chat.change_clicked.connect(self._show_change)
        self.chat.plan_inspect_requested.connect(self.inspector.show_plan)
        self.chat.detail_inspect_requested.connect(
            lambda t: self.inspector.show_text("过程细节", t)
        )

        self._refresh_sidebar()
        if not self.store.list_workspaces():
            self.chat.set_status("请先在侧栏添加工作目录（+）")

    # ---------- sidebar / session ----------

    def _refresh_sidebar(self) -> None:
        workspaces = self.store.list_workspaces(include_sessions=True)
        self.sidebar.populate(workspaces, self._session_id)

    @Slot(str)
    def _select_session(self, session_id: str) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "当前任务仍在运行")
            return
        try:
            detail = self.store.get_session(session_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "加载失败", str(exc))
            return
        self._session_id = session_id
        self._session = detail
        self._pending_ask = detail.get("pending_ask")
        self._apply_session_header(detail)
        self.chat.render_session(detail)
        self.inspector.clear()
        if self._pending_ask:
            self.chat.set_ask_visible(True)

    def _apply_session_header(self, session: dict[str, Any]) -> None:
        title = str(session.get("title") or session.get("preview") or "会话")
        self.chat.set_chat_title(title)
        wd = str(session.get("workdir") or "")
        # 只显示目录名，避免顶栏过长
        self.workdir_label.setText(Path(wd).name if wd else "")
        self.workdir_label.setToolTip(wd)
        mode = str(session.get("mode") or "agent")
        detail = str(session.get("detail") or "off")
        self.mode_combo.blockSignals(True)
        self.detail_combo.blockSignals(True)
        self.mode_combo.setCurrentText(mode if mode in {"agent", "plan"} else "agent")
        self.detail_combo.setCurrentText(
            detail if detail in {"off", "summary", "full"} else "off"
        )
        self.mode_combo.blockSignals(False)
        self.detail_combo.blockSignals(False)

    def _add_workspace(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if not path:
            return
        title, ok = QInputDialog.getText(self, "工作区名称", "可选显示名（可留空）")
        try:
            ws = self.store.add_workspace(path, title=title.strip() if ok and title.strip() else None)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "添加失败", str(exc))
            return
        self._refresh_sidebar()
        self._new_session(ws["id"])

    @Slot(str)
    def _new_session(self, workspace_id: str) -> None:
        if self._busy:
            return
        try:
            summary = self.store.create_session(workspace_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self._refresh_sidebar()
        self._select_session(summary["session_id"])

    @Slot(str, dict)
    def _patch_session(self, session_id: str, fields: dict) -> None:
        try:
            self.store.patch_session(session_id, **fields)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "更新失败", str(exc))
            return
        archived = bool(fields.get("archived"))
        self._refresh_sidebar()
        if session_id == self._session_id:
            if archived:
                self._clear_current_session()
            else:
                self._select_session(session_id)

    @Slot(str, dict)
    def _patch_workspace(self, workspace_id: str, fields: dict) -> None:
        try:
            self.store.patch_workspace(workspace_id, **fields)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "更新失败", str(exc))
            return
        self._refresh_sidebar()

    def _clear_current_session(self) -> None:
        self._session_id = None
        self._session = None
        self._pending_ask = None
        self.chat.clear()
        self.chat.set_chat_title("未选择会话")
        self.workdir_label.setText("")
        self.inspector.clear()

    def _delete_session(self) -> None:
        if not self._session_id:
            return
        self._delete_session_by_id(self._session_id)

    @Slot(str)
    def _delete_session_by_id(self, session_id: str) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "当前任务仍在运行")
            return
        if not session_id:
            return
        if (
            QMessageBox.question(self, "删除会话", "确定删除该对话？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.store.delete_session(session_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        if session_id == self._session_id:
            self._clear_current_session()
        self._refresh_sidebar()

    def _on_mode_changed(self, mode: str) -> None:
        if not self._session_id or self._busy:
            return
        try:
            detail = self.store.patch_session(self._session_id, mode=mode)
            self._session = detail
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "切换失败", str(exc))

    def _on_detail_changed(self, detail: str) -> None:
        if not self._session_id or self._busy:
            return
        try:
            self._session = self.store.patch_session(self._session_id, detail=detail)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "切换失败", str(exc))

    # ---------- run ----------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.chat.set_busy(busy)
        self.mode_combo.setEnabled(not busy)
        self.detail_combo.setEnabled(not busy)
        self.btn_delete.setEnabled(not busy)

    @Slot(str)
    def _send(self, text: str) -> None:
        if not self._session_id or self._busy:
            return
        self._start_run("chat", message=text, user_echo=text)

    def _confirm_plan(self) -> None:
        if not self._session_id or self._busy:
            return
        self._start_run("confirm", message="", user_echo="（确认执行计划）")

    def _start_run(self, kind: str, *, message: str, user_echo: str) -> None:
        assert self._session_id
        self._live_steps = []
        self._set_busy(True)
        self.chat.set_status("处理中…")
        self.chat.begin_live(user_echo)
        self.chat.set_pending_plan(None)
        self._run = RunThread(self.store, kind, self._session_id, message, self)  # type: ignore[arg-type]
        self._run.event_received.connect(self._on_event)
        self._run.run_finished.connect(self._on_run_finished)
        self._run.start()

    @Slot(object)
    def _on_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        if etype == "status":
            msg = str(event.get("message") or event.get("phase") or "")
            self.chat.set_status(msg or "处理中…")
            self.chat.update_live_status(msg)
        elif etype == "step":
            # replace or append by index
            idx = event.get("index")
            replaced = False
            if idx is not None:
                for i, old in enumerate(self._live_steps):
                    if old.get("index") == idx:
                        self._live_steps[i] = event
                        replaced = True
                        break
            if not replaced:
                self._live_steps.append(event)
            self.chat.update_live_steps(list(self._live_steps))
            todos = event.get("todos")
            if todos:
                self.chat.set_todos(todos)
        elif etype == "ask_user":
            self._pending_ask = event
            self.chat.set_ask_visible(True)
            self.chat.set_status("等待你的选择…")
            self._open_ask()
        elif etype == "done":
            session = event.get("session") or {}
            self._session = session
            self._session_id = str(session.get("session_id") or self._session_id)
            self._apply_session_header(session)
            self._pending_ask = session.get("pending_ask")
            self.chat.render_session(session)
            detail_text = session.get("detail_text")
            if detail_text:
                self.inspector.show_text("过程细节", str(detail_text))
            self._refresh_sidebar()
        elif etype == "error":
            self.chat.finish_live(str(event.get("message") or "未知错误"), ok=False)
            self.chat.set_status("出错")

    @Slot()
    def _on_run_finished(self) -> None:
        self._set_busy(False)
        if self.chat.status.text() not in {"出错"}:
            self.chat.set_status("就绪")
        self._run = None

    def _open_ask(self) -> None:
        if not self._session_id:
            return
        event = self._pending_ask
        if not event:
            event = self.store.get_pending_ask(self._session_id)
        if not event:
            QMessageBox.information(self, "确认", "当前没有待确认的提问")
            self.chat.set_ask_visible(False)
            return
        dlg = AskDialog(event, self)
        if dlg.exec() != AskDialog.DialogCode.Accepted:
            return
        ask_id = str(event.get("ask_id") or "")
        answers = dlg.answers_payload()
        try:
            self.store.answer_ask(
                self._session_id,
                ask_id=ask_id,
                answers=answers,
            )
        except Exception as exc:  # noqa: BLE001
            # fallback raw
            try:
                self.store.answer_ask(
                    self._session_id,
                    ask_id=ask_id,
                    raw=dlg.raw_fallback(),
                )
            except Exception as exc2:  # noqa: BLE001
                QMessageBox.warning(self, "提交失败", f"{exc}\n{exc2}")
                return
        self._pending_ask = None
        self.chat.set_ask_visible(False)
        self.chat.set_status("已提交确认，继续处理…")

    @Slot(dict)
    def _show_change(self, change: dict) -> None:
        workdir = ""
        if self._session:
            workdir = str(self._session.get("workdir") or "")
        self.inspector.show_change(change, workdir=workdir)
