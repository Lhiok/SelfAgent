"""多题 ask_user 对话框（对齐 web 工作台）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class AskDialog(QDialog):
    def __init__(self, event: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("需要你确认")
        self.resize(520, 460)
        self._event = event
        self._widgets: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        heading = QLabel("需要你确认")
        heading.setObjectName("SessionTitle")
        root.addWidget(heading)
        ctx = str(event.get("context") or "").strip()
        if ctx:
            tip = QLabel(ctx)
            tip.setWordWrap(True)
            tip.setObjectName("StatusLabel")
            root.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QVBoxLayout(body)
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
        form.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_question(self, q: dict[str, Any]) -> QWidget:
        box = QGroupBox(str(q.get("question") or "请选择"))
        layout = QVBoxLayout(box)
        options = [str(o) for o in (q.get("options") or [])]
        allow_multi = bool(q.get("allow_multiple", False))
        allow_custom = bool(q.get("allow_custom", True))
        default = q.get("default")
        radios: list[QRadioButton] = []
        checks: list[QCheckBox] = []
        if allow_multi:
            for opt in options:
                cb = QCheckBox(opt)
                if default is not None and str(default) == opt:
                    cb.setChecked(True)
                layout.addWidget(cb)
                checks.append(cb)
        else:
            for i, opt in enumerate(options):
                rb = QRadioButton(opt)
                if default is not None and str(default) == opt:
                    rb.setChecked(True)
                elif default is None and i == 0 and options:
                    rb.setChecked(True)
                layout.addWidget(rb)
                radios.append(rb)
        custom: QLineEdit | None = None
        if allow_custom:
            row = QHBoxLayout()
            row.addWidget(QLabel("其它"))
            custom = QLineEdit()
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
        return box

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
