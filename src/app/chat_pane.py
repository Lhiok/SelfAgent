"""中间聊天区：历史、直播步骤、todo、composer。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.theme import diff_line_stats, wrap_chat_html


class ChatPane(QWidget):
    send_requested = Signal(str)
    confirm_plan_requested = Signal()
    open_ask_requested = Signal()
    change_clicked = Signal(dict)
    plan_inspect_requested = Signal(dict)
    detail_inspect_requested = Signal(str)
    mode_changed = Signal(str)  # agent | plan

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPane")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("ChatHeader")
        head_l = QHBoxLayout(header)
        head_l.setContentsMargins(20, 10, 16, 10)
        self.chat_title = QLabel("未选择会话")
        self.chat_title.setObjectName("SessionTitle")
        head_l.addWidget(self.chat_title, 1)
        layout.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)

        self.todo_bar = QProgressBar()
        self.todo_bar.setTextVisible(False)
        self.todo_bar.setFixedHeight(3)
        self.todo_label = QLabel("")
        self.todo_label.setWordWrap(True)
        todo_wrap = QVBoxLayout()
        todo_wrap.setContentsMargins(12, 8, 12, 8)
        todo_wrap.setSpacing(6)
        todo_wrap.addWidget(self.todo_bar)
        todo_wrap.addWidget(self.todo_label)
        self.todo_widget = QWidget()
        self.todo_widget.setObjectName("TodoBanner")
        self.todo_widget.setLayout(todo_wrap)
        self.todo_widget.hide()
        todo_pad = QHBoxLayout()
        todo_pad.setContentsMargins(16, 0, 16, 0)
        todo_pad.addWidget(self.todo_widget)
        body.addLayout(todo_pad)

        self.view = QTextBrowser()
        self.view.setObjectName("ChatView")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        body.addWidget(self.view, 1)

        self.plan_row = QHBoxLayout()
        self.plan_row.setContentsMargins(16, 0, 16, 0)
        self.plan_row.setSpacing(8)
        self.btn_plan_detail = QPushButton("查看计划")
        self.btn_plan_detail.setObjectName("GhostButton")
        self.btn_confirm = QPushButton("确认执行")
        self.btn_confirm.setObjectName("PrimaryButton")
        self.btn_plan_detail.clicked.connect(self._emit_plan_detail)
        self.btn_confirm.clicked.connect(self.confirm_plan_requested.emit)
        self.plan_row.addWidget(self.btn_plan_detail)
        self.plan_row.addWidget(self.btn_confirm)
        self.plan_row.addStretch(1)
        self.plan_widget = QWidget()
        self.plan_widget.setObjectName("ActionStrip")
        self.plan_widget.setLayout(self.plan_row)
        self.plan_widget.hide()
        body.addWidget(self.plan_widget)

        self.ask_row = QHBoxLayout()
        self.ask_row.setContentsMargins(16, 0, 16, 0)
        self.btn_open_ask = QPushButton("打开确认面板")
        self.btn_open_ask.setObjectName("PrimaryButton")
        self.btn_open_ask.clicked.connect(self.open_ask_requested.emit)
        self.ask_row.addWidget(self.btn_open_ask)
        self.ask_row.addStretch(1)
        self.ask_widget = QWidget()
        self.ask_widget.setLayout(self.ask_row)
        self.ask_widget.hide()
        body.addWidget(self.ask_widget)

        layout.addLayout(body, 1)

        # 底部输入区（交互参照 Cursor Composer）
        dock = QWidget()
        dock.setObjectName("ComposerDock")
        dock_l = QVBoxLayout(dock)
        dock_l.setContentsMargins(16, 10, 16, 10)
        dock_l.setSpacing(6)

        composer_box = QWidget()
        composer_box.setObjectName("ComposerBox")
        composer_col = QVBoxLayout(composer_box)
        composer_col.setContentsMargins(12, 10, 12, 8)
        composer_col.setSpacing(4)

        self.input = QTextEdit()
        self.input.setPlaceholderText("发送后续消息…")
        self.input.setFixedHeight(44)
        composer_col.addWidget(self.input)

        # 底栏：左模式胶囊 · 右发送（参照 Cursor Composer）
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 2, 0, 0)
        tools.setSpacing(8)

        self._mode = "agent"
        self.btn_mode = QToolButton()
        self.btn_mode.setObjectName("ModePill")
        self.btn_mode.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_mode.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mode_menu = QMenu(self)
        self._mode_menu.setObjectName("ModeMenu")
        self._mode_menu.setFixedWidth(118)
        self._act_agent = QAction(self._mode_menu)
        self._act_plan = QAction(self._mode_menu)
        for act in (self._act_agent, self._act_plan):
            act.setCheckable(True)
        self._act_agent.triggered.connect(lambda: self._emit_mode("agent"))
        self._act_plan.triggered.connect(lambda: self._emit_mode("plan"))
        self._mode_menu.addAction(self._act_agent)
        self._mode_menu.addAction(self._act_plan)
        self.btn_mode.clicked.connect(self._show_mode_menu)
        self._paint_mode_pill()
        tools.addWidget(self.btn_mode)

        self.status = QLabel("就绪")
        self.status.setObjectName("StatusLabel")
        tools.addWidget(self.status, 1)

        self.btn_send = QPushButton("↑")
        self.btn_send.setObjectName("SendCircle")
        self.btn_send.setToolTip("发送（Ctrl+Enter）")
        self.btn_send.clicked.connect(self._emit_send)
        tools.addWidget(self.btn_send)
        composer_col.addLayout(tools)
        dock_l.addWidget(composer_box)
        layout.addWidget(dock)

        QShortcut(QKeySequence("Ctrl+Return"), self.input, self._emit_send)

        self._pending_plan: dict[str, Any] | None = None
        self._changes_by_key: dict[str, dict[str, Any]] = {}
        self._body_parts: list[str] = []
        self._live_steps: list[dict[str, Any]] = []

    def set_busy(self, busy: bool) -> None:
        self.btn_send.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.btn_confirm.setEnabled(not busy)
        self.btn_mode.setEnabled(not busy)
        if busy:
            self.btn_send.setText("■")
            self.btn_send.setToolTip("运行中…")
        else:
            self.btn_send.setText("↑")
            self.btn_send.setToolTip("发送（Ctrl+Enter）")

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_chat_title(self, title: str) -> None:
        self.chat_title.setText(title or "未选择会话")

    def set_mode(self, mode: str) -> None:
        self._mode = "plan" if str(mode or "").lower() == "plan" else "agent"
        self._paint_mode_pill()

    def _paint_mode_pill(self) -> None:
        label = "Plan" if self._mode == "plan" else "Agent"
        self.btn_mode.setText(f"∞ {label} ▾")
        self._act_agent.blockSignals(True)
        self._act_plan.blockSignals(True)
        self._act_agent.setChecked(self._mode == "agent")
        self._act_plan.setChecked(self._mode == "plan")
        self._act_agent.setText("∞  Agent" + ("  ✓" if self._mode == "agent" else ""))
        self._act_plan.setText("☰  Plan" + ("   ✓" if self._mode == "plan" else ""))
        self._act_agent.blockSignals(False)
        self._act_plan.blockSignals(False)

    def _show_mode_menu(self) -> None:
        """在胶囊上方弹出，圆角窄菜单。"""
        self._paint_mode_pill()
        self._mode_menu.adjustSize()
        btn = self.btn_mode
        global_tl = btn.mapToGlobal(QPoint(0, 0))
        mh = self._mode_menu.sizeHint().height()
        # 与胶囊左对齐，向上展开并留出空隙
        self._mode_menu.exec(QPoint(global_tl.x(), global_tl.y() - mh - 6))

    def _emit_mode(self, mode: str) -> None:
        self._mode = mode
        self._paint_mode_pill()
        self.mode_changed.emit(mode)

    def clear(self) -> None:
        self._body_parts = []
        self._live_steps = []
        self._changes_by_key.clear()
        self._pending_plan = None
        self.plan_widget.hide()
        self.ask_widget.hide()
        self.set_todos(None)
        self._paint()

    def render_session(self, session: dict[str, Any]) -> None:
        self.clear()
        parts: list[str] = []
        for turn in session.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            parts.append(_bubble("user", "你", str(turn.get("user") or "")))
            parts.append(_bubble("assistant", "助手", str(turn.get("answer") or "")))
            for ans in turn.get("ask_answers") or []:
                parts.append(f"<div class='meta'>确认 · {_escape(str(ans))}</div>")
            changes = turn.get("changes") or []
            if changes:
                parts.append(self._changes_html(changes, prefix=f"t{turn.get('index', 0)}"))
            detail = turn.get("detail_text") or ""
            if detail:
                key = f"detail-{turn.get('index')}"
                parts.append(
                    f"<div class='meta'><a href='detail:{key}'>查看过程细节</a></div>"
                )
                self._changes_by_key[key] = {"_detail": detail}
        plan = session.get("pending_plan")
        if isinstance(plan, dict) and plan.get("ok"):
            self._pending_plan = plan
            self.plan_widget.show()
            summary = _escape(
                str(plan.get("summary") or plan.get("text") or "待确认计划")[:240]
            )
            parts.append(f"<div class='plan-box'><b>待确认计划</b><br>{summary}</div>")
        if session.get("pending_ask"):
            self.ask_widget.show()
        self._body_parts = parts
        if not parts:
            self._body_parts = [
                "<div class='empty'><b>新会话</b>"
                "在下方输入任务开始。可切换 Agent / Plan，"
                "过程细节与文件改动在右侧检视。</div>"
            ]
        self.set_todos(session.get("todos"))
        self._paint()

    def begin_live(self, user_text: str) -> None:
        # drop empty placeholder
        if (
            len(self._body_parts) == 1
            and "empty" in self._body_parts[0]
        ):
            self._body_parts = []
        self._live_steps = []
        self._body_parts.append(_bubble("user", "你", user_text))
        self._body_parts.append(
            "<div class='live' id='live'><div class='role'>助手 · 进行中</div></div>"
        )
        self._paint()

    def update_live_status(self, message: str) -> None:
        inner = f"<div class='meta'>{_escape(message)}</div>{self._format_live_steps()}"
        self._set_live_inner(inner)

    def update_live_steps(self, steps: list[dict[str, Any]]) -> None:
        self._live_steps = steps
        for step in steps:
            for ch in step.get("changes") or []:
                if isinstance(ch, dict) and ch.get("path"):
                    self._changes_by_key[f"live:{ch['path']}"] = ch
        body = "".join(self._step_html(s) for s in steps)
        all_ch: list[dict[str, Any]] = []
        for step in steps:
            all_ch.extend(c for c in (step.get("changes") or []) if isinstance(c, dict))
        if all_ch:
            body += self._changes_html(all_ch, prefix="live")
        self._set_live_inner(body or "<div class='meta'>…</div>")

    def finish_live(self, answer: str, *, ok: bool = True) -> None:
        # remove live block
        self._body_parts = [p for p in self._body_parts if "id='live'" not in p]
        role = "助手" if ok else "错误"
        cls = "assistant" if ok else "error"
        self._body_parts.append(_bubble(cls, role, answer))
        self._live_steps = []
        self._paint()

    def set_pending_plan(self, plan: dict[str, Any] | None) -> None:
        self._pending_plan = plan if isinstance(plan, dict) and plan.get("ok") else None
        self.plan_widget.setVisible(self._pending_plan is not None)

    def set_ask_visible(self, visible: bool) -> None:
        self.ask_widget.setVisible(visible)

    def set_todos(self, todos: dict[str, Any] | None) -> None:
        if not todos or not isinstance(todos, dict):
            self.todo_widget.hide()
            return
        items = todos.get("items") or []
        counts = todos.get("counts") or {}
        total = int(counts.get("total") or len(items) or 0)
        done = int(counts.get("done") or 0)
        if total <= 0 and not items:
            self.todo_widget.hide()
            return
        self.todo_bar.setMaximum(max(total, 1))
        self.todo_bar.setValue(min(done, total))
        current = ""
        for it in items:
            if isinstance(it, dict) and it.get("status") == "in_progress":
                current = str(it.get("text") or it.get("title") or "")
                break
        if not current and items:
            for it in items:
                if isinstance(it, dict) and it.get("status") not in {"done", "completed"}:
                    current = str(it.get("text") or it.get("title") or "")
                    break
        self.todo_label.setText(
            f"任务 {done}/{total}" + (f"  ·  {current}" if current else "")
        )
        self.todo_widget.show()

    def _emit_send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def _emit_plan_detail(self) -> None:
        if self._pending_plan:
            self.plan_inspect_requested.emit(self._pending_plan)

    def _on_anchor(self, url) -> None:
        href = url.toString()
        if href.startswith("change:"):
            key = href[len("change:") :]
            ch = self._changes_by_key.get(key)
            if ch and "_detail" not in ch:
                self.change_clicked.emit(ch)
        elif href.startswith("detail:"):
            key = href[len("detail:") :]
            payload = self._changes_by_key.get(key) or {}
            detail = str(payload.get("_detail") or "")
            if detail:
                self.detail_inspect_requested.emit(detail)

    def _changes_html(self, changes: list[dict[str, Any]], *, prefix: str) -> str:
        n = len(changes)
        bits = [
            f"<div class='changes'><div class='changes-head'>"
            f"{n} 个文件已更改</div>"
        ]
        for i, ch in enumerate(changes):
            path = str(ch.get("path") or "?")
            key = f"{prefix}:{path}:{i}"
            self._changes_by_key[key] = ch
            plus, minus = diff_line_stats(str(ch.get("diff") or ""))
            stats = ""
            if plus:
                stats += f"<span class='plus'>+{plus}</span>"
            if minus:
                stats += f"<span class='minus'>-{minus}</span>"
            name = path.replace("\\", "/").split("/")[-1] or path
            bits.append(
                f"<a class='change-row' href='change:{key}'>"
                f"<span class='change-path'>{_escape(name)}</span>{stats}</a>"
            )
        bits.append("</div>")
        return "".join(bits)

    def _step_html(self, step: dict[str, Any]) -> str:
        idx = step.get("index", "?")
        thought = str(step.get("thought") or "").strip()
        actions = step.get("actions") or []
        final = str(step.get("final_answer") or "").strip()
        parts = [f"<div class='step'><div class='step-title'>步骤 {idx}</div>"]
        if thought:
            short = thought if len(thought) < 280 else thought[:280] + "…"
            parts.append(f"<div class='thought'>{_escape(short)}</div>")
        for act in actions:
            if not isinstance(act, dict):
                continue
            name = str(act.get("action") or "")
            op = str(act.get("op") or "")
            path = str(act.get("path") or "")
            chip = name
            if op:
                chip += f" · {op}"
            if path:
                chip += f" · {path}"
            ok = act.get("ok")
            mark = "✓" if ok else ("…" if ok is None else "✗")
            parts.append(f"<span class='chip'>{mark} {_escape(chip)}</span>")
        if final:
            parts.append(f"<div style='margin-top:6px'>{_escape(final)}</div>")
        parts.append("</div>")
        return "".join(parts)

    def _format_live_steps(self) -> str:
        return "".join(self._step_html(s) for s in self._live_steps)

    def _set_live_inner(self, inner: str) -> None:
        live = (
            "<div class='live' id='live'>"
            "<div class='role'>助手 · 进行中</div>"
            f"{inner}</div>"
        )
        replaced = False
        for i, part in enumerate(self._body_parts):
            if "id='live'" in part:
                self._body_parts[i] = live
                replaced = True
                break
        if not replaced:
            self._body_parts.append(live)
        self._paint()

    def _paint(self) -> None:
        self.view.setHtml(wrap_chat_html("".join(self._body_parts)))
        self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.view.setTextCursor(cursor)


def _bubble(cls: str, role: str, text: str) -> str:
    return (
        f"<div class='msg {cls}'>"
        f"<div class='role'>{_escape(role)}</div>"
        f"<div class='bubble'>{_escape(text)}</div>"
        f"</div>"
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
