"""中间聊天区：历史、直播步骤、todo、composer。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ChatPane(QWidget):
    send_requested = Signal(str)
    confirm_plan_requested = Signal()
    open_ask_requested = Signal()
    change_clicked = Signal(dict)
    plan_inspect_requested = Signal(dict)
    detail_inspect_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.todo_bar = QProgressBar()
        self.todo_bar.setMaximumHeight(14)
        self.todo_bar.setTextVisible(True)
        self.todo_label = QLabel("")
        self.todo_label.setWordWrap(True)
        todo_wrap = QVBoxLayout()
        todo_wrap.setContentsMargins(0, 0, 0, 0)
        todo_wrap.addWidget(self.todo_bar)
        todo_wrap.addWidget(self.todo_label)
        self.todo_widget = QWidget()
        self.todo_widget.setLayout(todo_wrap)
        self.todo_widget.hide()
        layout.addWidget(self.todo_widget)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        layout.addWidget(self.view, 1)

        self.plan_row = QHBoxLayout()
        self.btn_plan_detail = QPushButton("查看计划细节")
        self.btn_confirm = QPushButton("确认执行")
        self.btn_plan_detail.clicked.connect(self._emit_plan_detail)
        self.btn_confirm.clicked.connect(self.confirm_plan_requested.emit)
        self.plan_row.addWidget(self.btn_plan_detail)
        self.plan_row.addWidget(self.btn_confirm)
        self.plan_row.addStretch(1)
        self.plan_widget = QWidget()
        self.plan_widget.setLayout(self.plan_row)
        self.plan_widget.hide()
        layout.addWidget(self.plan_widget)

        self.ask_row = QHBoxLayout()
        self.btn_open_ask = QPushButton("打开确认面板")
        self.btn_open_ask.clicked.connect(self.open_ask_requested.emit)
        self.ask_row.addWidget(self.btn_open_ask)
        self.ask_row.addStretch(1)
        self.ask_widget = QWidget()
        self.ask_widget.setLayout(self.ask_row)
        self.ask_widget.hide()
        layout.addWidget(self.ask_widget)

        composer = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setPlaceholderText("输入任务，Ctrl+Enter 发送…")
        self.input.setFixedHeight(72)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._emit_send)
        composer.addWidget(self.input, 1)
        composer.addWidget(self.btn_send)
        layout.addLayout(composer)

        self.status = QLabel("就绪")
        layout.addWidget(self.status)

        QShortcut(QKeySequence("Ctrl+Return"), self.input, self._emit_send)

        self._pending_plan: dict[str, Any] | None = None
        self._changes_by_key: dict[str, dict[str, Any]] = {}
        self._live_html = ""
        self._live_steps: list[dict[str, Any]] = []

    def set_busy(self, busy: bool) -> None:
        self.btn_send.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.btn_confirm.setEnabled(not busy)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def clear(self) -> None:
        self.view.clear()
        self._live_html = ""
        self._changes_by_key.clear()
        self._pending_plan = None
        self.plan_widget.hide()
        self.ask_widget.hide()
        self.set_todos(None)

    def render_session(self, session: dict[str, Any]) -> None:
        self.clear()
        parts: list[str] = []
        for turn in session.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            user = _escape(str(turn.get("user") or ""))
            answer = _escape(str(turn.get("answer") or ""))
            parts.append(f"<p><b>你</b><br>{user}</p>")
            parts.append(f"<p><b>助手</b><br>{answer}</p>")
            for ans in turn.get("ask_answers") or []:
                parts.append(
                    f"<p style='color:#666'><i>确认</i> {_escape(str(ans))}</p>"
                )
            changes = turn.get("changes") or []
            if changes:
                parts.append(self._changes_html(changes, prefix=f"t{turn.get('index', 0)}"))
            detail = turn.get("detail_text") or ""
            if detail:
                key = f"detail-{turn.get('index')}"
                parts.append(
                    f"<p><a href='detail:{key}'>查看过程细节</a></p>"
                )
                self._changes_by_key[key] = {"_detail": detail}
        self.view.setHtml("".join(parts) or "<p style='color:#888'>新会话，发送第一条任务开始。</p>")
        plan = session.get("pending_plan")
        if isinstance(plan, dict) and plan.get("ok"):
            self._pending_plan = plan
            self.plan_widget.show()
            summary = _escape(str(plan.get("summary") or plan.get("text") or "待确认计划")[:240])
            cur = self.view.toHtml()
            self.view.setHtml(cur + f"<p><b>待确认计划</b><br>{summary}</p>")
        if session.get("pending_ask"):
            self.ask_widget.show()
        self.set_todos(session.get("todos"))
        self._scroll_bottom()

    def begin_live(self, user_text: str) -> None:
        self._live_html = ""
        self._append_html(f"<p><b>你</b><br>{_escape(user_text)}</p>")
        self._append_html("<div id='live'><p><b>助手 · 进行中</b></p></div>")

    def update_live_status(self, message: str) -> None:
        self._set_live_body(f"<p style='color:#555'>{_escape(message)}</p>{self._format_live_steps()}")

    def update_live_steps(self, steps: list[dict[str, Any]]) -> None:
        self._live_steps = steps
        body = "".join(self._step_html(s) for s in steps)
        self._set_live_body(body or "<p style='color:#555'>…</p>")
        # aggregate changes
        for step in steps:
            for ch in step.get("changes") or []:
                if isinstance(ch, dict):
                    path = str(ch.get("path") or "")
                    if path:
                        self._changes_by_key[f"live:{path}"] = ch
        if any(step.get("changes") for step in steps):
            all_ch: list[dict[str, Any]] = []
            for step in steps:
                all_ch.extend(c for c in (step.get("changes") or []) if isinstance(c, dict))
            if all_ch:
                self._set_live_body(
                    "".join(self._step_html(s) for s in steps)
                    + self._changes_html(all_ch, prefix="live")
                )

    def finish_live(self, answer: str, *, ok: bool = True) -> None:
        role = "助手" if ok else "错误"
        self._append_html(f"<p><b>{role}</b><br>{_escape(answer)}</p>")
        self._live_html = ""
        self._live_steps = []

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
        self.todo_bar.setFormat(f"Todos {done}/{total}")
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
        self.todo_label.setText(current)
        self.todo_widget.show()

    # --- internals ---

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
        bits = ["<p><b>文件改动</b><ul>"]
        for i, ch in enumerate(changes):
            path = str(ch.get("path") or "?")
            kind = str(ch.get("kind") or "")
            key = f"{prefix}:{path}:{i}"
            self._changes_by_key[key] = ch
            bits.append(
                f"<li><a href='change:{key}'>[{_escape(kind)}] {_escape(path)}</a></li>"
            )
        bits.append("</ul></p>")
        return "".join(bits)

    def _step_html(self, step: dict[str, Any]) -> str:
        idx = step.get("index", "?")
        thought = str(step.get("thought") or "").strip()
        actions = step.get("actions") or []
        final = str(step.get("final_answer") or "").strip()
        parts = [f"<p><b>步骤 {idx}</b>"]
        if thought:
            short = thought if len(thought) < 280 else thought[:280] + "…"
            parts.append(f"<br><i>{_escape(short)}</i>")
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
            parts.append(f"<br><code>{mark} {_escape(chip)}</code>")
        if final:
            parts.append(f"<br>{_escape(final)}")
        parts.append("</p>")
        return "".join(parts)

    def _format_live_steps(self) -> str:
        return "".join(self._step_html(s) for s in getattr(self, "_live_steps", []) or [])

    def _append_html(self, html: str) -> None:
        cur = self.view.toHtml()
        # crude append before closing body if present
        if "</body>" in cur:
            cur = cur.replace("</body>", html + "</body>")
            self.view.setHtml(cur)
        else:
            self.view.setHtml(cur + html)
        self._scroll_bottom()

    def _set_live_body(self, inner: str) -> None:
        # rewrite last live block by re-append strategy: keep history without live, then add live
        # Simpler: just append status updates as paragraphs during run
        self._live_html = inner
        # Find marker — if missing, append
        base = self.view.toHtml()
        marker_start = base.rfind("<div id='live'>")
        if marker_start >= 0:
            marker_end = base.find("</div>", marker_start)
            if marker_end >= 0:
                new_html = (
                    base[:marker_start]
                    + f"<div id='live'><p><b>助手 · 进行中</b></p>{inner}</div>"
                    + base[marker_end + 6 :]
                )
                self.view.setHtml(new_html)
                self._scroll_bottom()
                return
        self._append_html(f"<div id='live'><p><b>助手 · 进行中</b></p>{inner}</div>")

    def _scroll_bottom(self) -> None:
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.view.setTextCursor(cursor)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
