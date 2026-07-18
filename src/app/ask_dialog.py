"""多题 ask_user 对话框（与主界面风格一致）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class AskDialog(QDialog):
    def __init__(self, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("需要你确认")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizeGripEnabled(True)
        self._event = event
        self._widgets: list[dict[str, Any]] = []
        self._fitted = False

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        self._root = root

        heading = QLabel("需要你确认")
        heading.setObjectName("AppDialogTitle")
        root.addWidget(heading)
        tip = QLabel("请选择后继续，Agent 会按你的选择执行。")
        tip.setObjectName("AppDialogMessage")
        tip.setWordWrap(True)
        root.addWidget(tip)
        ctx = str(event.get("context") or "").strip()
        if ctx:
            ctx_lab = QLabel(ctx)
            ctx_lab.setWordWrap(True)
            ctx_lab.setObjectName("StatusLabel")
            root.addWidget(ctx_lab)

        scroll = QScrollArea()
        scroll.setObjectName("AskScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        body = QWidget()
        body.setObjectName("AskScrollHost")
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 0, 4, 0)
        form.setSpacing(10)
        questions = event.get("questions")
        if not isinstance(questions, list) or not questions:
            questions = [
                {
                    "id": "1",
                    "question": event.get("question") or "请选择",
                    "options": list(event.get("options") or []),
                    "allow_multiple": bool(event.get("allow_multiple", False)),
                    "allow_custom": bool(event.get("allow_custom", True)),
                    "default": event.get("default"),
                }
            ]
        for q in questions:
            form.addWidget(self._build_question(q if isinstance(q, dict) else {}))
        scroll.setWidget(body)
        root.addWidget(scroll, 0)
        self._scroll = scroll
        self._body = body

        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("DialogGhostButton")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setObjectName("DialogPrimaryButton")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        root.addLayout(btns)
        self._btns = btns

        self.setMinimumWidth(420)
        self.setMaximumWidth(640)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            QTimer.singleShot(0, self._fit_to_content)

    def _fit_to_content(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        max_h = int((avail.height() if avail else 800) * 0.78)
        max_w = min(560, int((avail.width() if avail else 900) * 0.9))
        target_w = max(self.minimumWidth(), min(520, max_w))
        self.setMaximumWidth(max_w)

        margins = self._root.contentsMargins()
        chrome = margins.top() + margins.bottom()
        spacing = self._root.spacing()
        for i in range(self._root.count()):
            item = self._root.itemAt(i)
            if item is None:
                continue
            w = item.widget()
            if w is self._scroll:
                chrome += spacing
                continue
            if w is not None:
                chrome += w.sizeHint().height() + spacing
            elif item.layout() is not None:
                chrome += item.layout().sizeHint().height() + spacing

        inner_w = max(280, target_w - margins.left() - margins.right() - 8)
        self._body.setMinimumWidth(inner_w)
        self._body.adjustSize()
        needed = max(self._body.sizeHint().height(), self._body.minimumSizeHint().height())

        scroll_max = max(80, max_h - chrome)
        scroll_h = min(needed, scroll_max)
        # 内容刚好放下时略留余量，避免无故出现滚动条
        if needed <= scroll_max:
            scroll_h = needed
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFixedHeight(scroll_h)

        total_h = chrome + scroll_h
        self.resize(target_w, min(total_h, max_h))
        self.setMinimumHeight(min(220, total_h))
        self.setMaximumHeight(max_h)

    def _build_question(self, q: dict[str, Any]) -> QWidget:
        card = QWidget()
        card.setObjectName("AskQuestionCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        q_lab = QLabel(str(q.get("question") or "请选择"))
        q_lab.setObjectName("AskQuestionTitle")
        q_lab.setWordWrap(True)
        layout.addWidget(q_lab)

        options = [str(o) for o in (q.get("options") or [])]
        allow_multi = bool(q.get("allow_multiple", False))
        allow_custom = bool(q.get("allow_custom", True))
        default = q.get("default")
        radios: list[QRadioButton] = []
        checks: list[QCheckBox] = []
        if allow_multi:
            for opt in options:
                cb = QCheckBox(opt)
                cb.setObjectName("AskOption")
                cb.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Minimum,
                )
                if default is not None and str(default) == opt:
                    cb.setChecked(True)
                layout.addWidget(cb)
                checks.append(cb)
        else:
            for i, opt in enumerate(options):
                rb = QRadioButton(opt)
                rb.setObjectName("AskOption")
                rb.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Minimum,
                )
                if default is not None and str(default) == opt:
                    rb.setChecked(True)
                elif default is None and i == 0 and options:
                    rb.setChecked(True)
                layout.addWidget(rb)
                radios.append(rb)
        custom: QLineEdit | None = None
        if allow_custom:
            row = QHBoxLayout()
            row.setSpacing(8)
            other = QLabel("其它")
            other.setObjectName("AskOtherLabel")
            row.addWidget(other)
            custom = QLineEdit()
            custom.setObjectName("AskCustomInput")
            custom.setPlaceholderText("自定义回答…")
            row.addWidget(custom, 1)
            layout.addLayout(row)
        self._widgets.append(
            {
                "id": str(q.get("id") or ""),
                "allow_multiple": allow_multi,
                "radios": radios,
                "checks": checks,
                "custom": custom,
            }
        )
        return card

    def answers_payload(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for w in self._widgets:
            selected: list[str] = []
            if w["allow_multiple"]:
                selected = [c.text() for c in w["checks"] if c.isChecked()]
            else:
                for rb in w["radios"]:
                    if rb.isChecked():
                        selected = [rb.text()]
                        break
            custom = w["custom"]
            custom_text = custom.text().strip() if custom is not None else ""
            if custom_text:
                selected = selected + [custom_text] if w["allow_multiple"] else [custom_text]
            value: Any = selected if w["allow_multiple"] else (selected[0] if selected else "")
            out.append({"id": w["id"], "value": value})
        return out

    def raw_fallback(self) -> str:
        answers = self.answers_payload()
        if len(answers) == 1:
            v = answers[0].get("value")
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return str(v or "")
        import json

        return json.dumps(answers, ensure_ascii=False)
