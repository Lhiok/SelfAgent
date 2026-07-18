"""右侧检视面板：diff / plan / detail。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Inspector(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        caption = QLabel("检视")
        caption.setObjectName("PanelCaption")
        head = QHBoxLayout()
        self.title = QLabel("未选择")
        self.title.setObjectName("SessionTitle")
        self.btn_open = QPushButton("打开文件")
        self.btn_open.setObjectName("GhostButton")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_path)
        head.addWidget(self.title, 1)
        head.addWidget(self.btn_open)
        layout.addWidget(caption)
        layout.addLayout(head)
        self.view = QPlainTextEdit()
        self.view.setObjectName("InspectorView")
        self.view.setReadOnly(True)
        font = QFont("Cascadia Mono")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.view.setFont(font)
        layout.addWidget(self.view, 1)
        self._path: str | None = None

    def clear(self) -> None:
        self.title.setText("未选择")
        self.view.clear()
        self._path = None
        self.btn_open.setEnabled(False)

    def show_text(self, title: str, text: str, *, path: str | None = None) -> None:
        self.title.setText(title)
        self.view.setPlainText(text or "")
        self._path = path
        self.btn_open.setEnabled(bool(path))

    def show_change(self, change: dict[str, Any], *, workdir: str = "") -> None:
        path = str(change.get("path") or "")
        kind = str(change.get("kind") or "change")
        diff = str(change.get("diff") or "")
        if not diff:
            old = str(change.get("old_text") or "")
            new = str(change.get("new_text") or "")
            diff = f"--- old\n+++ new\n{old}\n---\n{new}"
        abs_path = path
        if path and workdir and not _is_abs(path):
            from pathlib import Path

            abs_path = str((Path(workdir) / path).resolve())
        self.show_text(f"{kind}: {path}", diff, path=abs_path or None)

    def show_plan(self, plan: dict[str, Any]) -> None:
        lines = [str(plan.get("summary") or "计划"), ""]
        text = str(plan.get("text") or "")
        if text:
            lines.append(text)
        steps = plan.get("steps") or []
        if steps:
            lines.append("")
            lines.append("步骤:")
            for i, step in enumerate(steps, 1):
                if isinstance(step, dict):
                    lines.append(f"{i}. {step.get('title') or step.get('action') or step}")
                else:
                    lines.append(f"{i}. {step}")
        self.show_text("计划细节", "\n".join(lines))

    def _open_path(self) -> None:
        if not self._path:
            return
        from pathlib import Path

        p = Path(self._path)
        if p.is_file() or p.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            return
        # vscode deep link fallback
        url = QUrl(f"vscode://file/{self._path.replace(chr(92), '/')}")
        QDesktopServices.openUrl(url)


def _is_abs(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_absolute()
