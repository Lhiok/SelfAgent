"""Windows 桌面主窗口。"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config as cfg
from log import reset_logger
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import AskUserSkill, SkillRegistry


class MainWindow(QMainWindow):
    ask_requested = Signal(str, list, object)  # question, options, reply_queue
    chat_finished = Signal(str, bool)  # answer, ok
    status_changed = Signal(str)

    def __init__(self, *, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("SelfAgent")
        self.resize(960, 640)

        root = Path(__file__).resolve().parents[2]
        cfg_path = config_path or (root / "config.yaml")
        if cfg_path.is_file():
            cfg.load_config(cfg_path)
        reset_logger()

        self._busy = False
        self._worker: threading.Thread | None = None
        self._conv: Conversation | None = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        row = QHBoxLayout()
        self.workdir_edit = QLineEdit(str(Path.cwd()))
        btn_browse = QPushButton("选择目录")
        btn_browse.clicked.connect(self._pick_workdir)
        btn_new = QPushButton("新建会话")
        btn_new.clicked.connect(self._new_session)
        row.addWidget(QLabel("工作目录"))
        row.addWidget(self.workdir_edit, 1)
        row.addWidget(btn_browse)
        row.addWidget(btn_new)
        layout.addLayout(row)

        self.messages = QTextEdit()
        self.messages.setReadOnly(True)
        layout.addWidget(self.messages, 1)

        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("输入任务，Enter 发送…")
        self.input_edit.returnPressed.connect(self._send)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._send)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.btn_send)
        layout.addLayout(input_row)

        self.status = QLabel("就绪")
        layout.addWidget(self.status)

        self.ask_requested.connect(self._on_ask, Qt.ConnectionType.QueuedConnection)
        self.chat_finished.connect(self._on_chat_finished)
        self.status_changed.connect(self.status.setText)

        self._new_session()

    def _pick_workdir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择工作目录", self.workdir_edit.text())
        if path:
            self.workdir_edit.setText(path)
            if self._conv is not None:
                try:
                    self._conv.set_workdir(path)
                    self._append("系统", f"工作目录: {path}")
                except (OSError, ValueError) as exc:
                    QMessageBox.warning(self, "切换失败", str(exc))

    def _new_session(self) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "当前任务仍在运行")
            return
        workdir = self.workdir_edit.text().strip() or "."
        ask_skill = AskUserSkill(ask_handler=self._ask_handler)
        skills = SkillRegistry.from_config(workdir=workdir, load_permission=False)
        skills.register(ask_skill)
        agent = ReActAgent(
            skills=skills,
            permission=PermissionGuard.from_config(),
            mode=AgentMode.AGENT,
            workdir=workdir,
        )
        self._conv = Conversation(agent)
        self.messages.clear()
        self._append("系统", f"新会话 {self._conv.session_id[:8]} · {agent.workdir}")
        self.status_changed.emit("就绪")

    def _ask_handler(self, question: str, options: list[str], meta: dict[str, Any]) -> str:
        reply_q: queue.Queue[str] = queue.Queue(maxsize=1)
        self.ask_requested.emit(question, list(options or []), reply_q)
        try:
            return reply_q.get(timeout=600)
        except queue.Empty:
            return ""

    @Slot(str, list, object)
    def _on_ask(self, question: str, options: list, reply_q: object) -> None:
        assert isinstance(reply_q, queue.Queue)
        opts = [str(o) for o in options] if options else ["确定"]
        item, ok = QInputDialog.getItem(
            self,
            "需要你确认",
            question or "请选择",
            opts,
            0,
            False,
        )
        reply_q.put(item if ok else "")

    def _send(self) -> None:
        if self._busy or self._conv is None:
            return
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        self._append("你", text)
        self._busy = True
        self.btn_send.setEnabled(False)
        self.status_changed.emit("处理中…")

        conv = self._conv

        def worker() -> None:
            try:
                result = conv.chat(text)
                self.chat_finished.emit(result.answer or "", True)
            except Exception as exc:  # noqa: BLE001
                self.chat_finished.emit(str(exc), False)

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    @Slot(str, bool)
    def _on_chat_finished(self, answer: str, ok: bool) -> None:
        self._busy = False
        self.btn_send.setEnabled(True)
        self.status_changed.emit("就绪" if ok else "出错")
        self._append("助手" if ok else "错误", answer)

    def _append(self, role: str, text: str) -> None:
        self.messages.append(f"<b>{role}</b><br>{_escape(text)}<br>")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
