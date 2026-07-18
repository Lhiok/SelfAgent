"""右侧检视面板：改动 diff / 计划 / 细节（参照 Cursor Changes）。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.theme import diff_line_stats, wrap_diff_html


class Inspector(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Inspector")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(12, 10, 12, 8)
        caption = QLabel("改动")
        caption.setObjectName("PanelCaption")
        self.btn_open = QPushButton("打开文件")
        self.btn_open.setObjectName("GhostButton")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_path)
        bar_l.addWidget(caption)
        bar_l.addStretch(1)
        bar_l.addWidget(self.btn_open)
        layout.addWidget(bar)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(12, 0, 12, 8)
        self.title = QLabel("未选择文件")
        self.title.setObjectName("SessionTitle")
        self.title.setWordWrap(True)
        self.stats = QLabel("")
        self.stats.setObjectName("StatusLabel")
        path_row.addWidget(self.title, 1)
        path_row.addWidget(self.stats)
        layout.addLayout(path_row)

        self.view = QTextBrowser()
        self.view.setObjectName("InspectorView")
        self.view.setOpenExternalLinks(False)
        font = QFont("Cascadia Mono")
        if not font.exactMatch():
            font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.view.setFont(font)
        pad = QVBoxLayout()
        pad.setContentsMargins(12, 0, 12, 12)
        pad.addWidget(self.view, 1)
        layout.addLayout(pad, 1)
        self._path: str | None = None

    def clear(self) -> None:
        self.title.setText("未选择文件")
        self.stats.setText("")
        self.view.clear()
        self._path = None
        self.btn_open.setEnabled(False)

    def show_text(self, title: str, text: str, *, path: str | None = None) -> None:
        self.title.setText(title)
        self.stats.setText("")
        esc = (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self.view.setHtml(
            "<html><body style='font-family:Segoe UI;padding:12px;color:#1A1A1A'>"
            f"{esc}</body></html>"
        )
        self._path = path
        self.btn_open.setEnabled(bool(path))

    def show_change(self, change: dict[str, Any], *, workdir: str = "") -> None:
        path = str(change.get("path") or "")
        diff = str(change.get("diff") or "")
        if not diff:
            old = str(change.get("old_text") or "")
            new = str(change.get("new_text") or "")
            diff = f"--- a/{path}\n+++ b/{path}\n"
            for line in old.splitlines():
                diff += f"-{line}\n"
            for line in new.splitlines():
                diff += f"+{line}\n"
        abs_path = path
        if path and workdir and not _is_abs(path):
            from pathlib import Path

            abs_path = str((Path(workdir) / path).resolve())
        name = path.replace("\\", "/").split("/")[-1] or path or "未命名"
        plus, minus = diff_line_stats(diff)
        self.title.setText(name)
        stats = []
        if plus:
            stats.append(f"+{plus}")
        if minus:
            stats.append(f"-{minus}")
        self.stats.setText("  ".join(stats))
        self.view.setHtml(wrap_diff_html(diff))
        self._path = abs_path or None
        self.btn_open.setEnabled(bool(self._path))

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
        url = QUrl(f"vscode://file/{self._path.replace(chr(92), '/')}")
        QDesktopServices.openUrl(url)


def _is_abs(path: str) -> bool:
    from pathlib import Path

    return Path(path).is_absolute()
