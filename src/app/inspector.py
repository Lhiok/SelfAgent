"""右侧检视面板：改动 diff / 计划 / 细节（参照 Cursor Changes）。"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.markdown_render import render_markdown
from app.theme import (
    apply_full_width_diff_backgrounds,
    build_diff_table_html,
    diff_line_stats,
    esc_html,
    pick_ui_mono_font,
    wrap_diff_html,
    wrap_plan_html,
)


def _rel_path(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")


_SKILL_LABELS = {
    "local_file": "文件",
    "shell_run": "终端",
    "search_code": "搜索",
    "git_ops": "Git",
    "ask_user": "询问用户",
    "feishu_notify": "飞书通知",
}

_ACTION_LABELS = {
    "patch": "修改文件",
    "write": "写入文件",
    "create": "创建文件",
    "read": "读取文件",
    "list": "列出目录",
    "delete": "删除",
    "run": "运行命令",
}


def _step_title(skill: str, action: str, index: int) -> str:
    act = _ACTION_LABELS.get(action, action or "操作")
    sk = _SKILL_LABELS.get(skill, skill or "步骤")
    if action:
        return f"步骤 {index} · {act}"
    return f"步骤 {index} · {sk}"


def _make_unified_diff(path: str, old_text: str, new_text: str) -> str:
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    rel = _rel_path(path) or "file"
    return "\n".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
    )


def _is_markdown_path(path: str) -> bool:
    name = _rel_path(path).lower()
    return name.endswith((".md", ".markdown", ".mdx"))


def _looks_like_markdown(text: str) -> bool:
    s = (text or "").lstrip()
    if not s:
        return False
    if s.startswith("#") or s.startswith("```"):
        return True
    return bool(
        "\n## " in text
        or "\n### " in text
        or "\n- " in text
        or "\n* " in text
        or "```" in text
    )


def _render_plan_content(path: str, content: str) -> str:
    """写入内容：Markdown 走渲染，其余等宽原文。"""
    preview = content if len(content) <= 8000 else content[:8000] + "\n\n…"
    if _is_markdown_path(path) or _looks_like_markdown(preview):
        frag = render_markdown(preview)
        if frag.strip():
            return f"<div class='plan-md'>{frag}</div>"
    return f"<pre class='plan-pre'>{esc_html(preview)}</pre>"


def _format_plan_step_html(step: dict[str, Any], index: int) -> str:
    skill = str(step.get("skill") or "")
    args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
    action = str(args.get("action") or "")
    path = str(args.get("path") or "")
    why = str(step.get("why") or args.get("why") or "").strip()
    parts = [
        f"<div class='plan-step'>",
        f"<div class='plan-step-head'>{esc_html(_step_title(skill, action, index))}</div>",
    ]
    if why:
        parts.append(f"<div class='plan-step-why'>{esc_html(why)}</div>")
    if path:
        parts.append(f"<div class='plan-path'>{esc_html(_rel_path(path))}</div>")

    if action == "patch" or (args.get("old_text") is not None and args.get("new_text") is not None):
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        diff = _make_unified_diff(path or "file", old_text, new_text)
        plus, minus = diff_line_stats(diff)
        stats = []
        if plus:
            stats.append(f"<span class='plus'>+{plus}</span>")
        if minus:
            stats.append(f"<span class='minus'>-{minus}</span>")
        if stats:
            parts.append(f"<div class='plan-stats'>{' '.join(stats)}</div>")
        parts.append(build_diff_table_html(diff))
    elif action in {"write", "create"} and args.get("content") is not None:
        content = str(args.get("content") or "")
        parts.append(_render_plan_content(path, content))
    elif action in {"run", ""} and (args.get("command") or args.get("cmd")):
        cmd = str(args.get("command") or args.get("cmd") or "")
        parts.append(f"<pre class='plan-pre'>{esc_html(cmd)}</pre>")
    else:
        # 其余参数：展示可读键值，跳过超长文本
        bits: list[str] = []
        for k, v in args.items():
            if k in {"old_text", "new_text", "content", "why", "action", "path"}:
                continue
            text = str(v)
            if len(text) > 200:
                text = text[:200] + "…"
            bits.append(f"{k}: {text}")
        if bits:
            parts.append(f"<pre class='plan-pre'>{esc_html(chr(10).join(bits))}</pre>")
        elif not path:
            parts.append("<div class='plan-step-meta'>（无额外参数）</div>")

    parts.append("</div>")
    return "".join(parts)


def format_plan_html(plan: dict[str, Any]) -> str:
    summary = str(plan.get("summary") or "").strip() or "待确认计划"
    summary_frag = render_markdown(summary) if _looks_like_markdown(summary) else ""
    if summary_frag.strip():
        body = [f"<div class='plan-summary plan-md'>{summary_frag}</div>"]
    else:
        body = [f"<div class='plan-summary'>{esc_html(summary)}</div>"]
    steps = plan.get("steps") or []
    if not steps:
        # 兼容仅有 text 的旧数据：不再原样倾倒 JSON
        text = str(plan.get("text") or "").strip()
        if text and not text.startswith("Plan:"):
            body.append(_render_plan_content("plan.md", text))
        elif not steps:
            body.append("<div class='plan-step-meta'>暂无具体步骤</div>")
    for i, step in enumerate(steps, 1):
        if isinstance(step, dict):
            idx = int(step.get("index") or i)
            body.append(_format_plan_step_html(step, idx))
        else:
            body.append(
                f"<div class='plan-step'><div class='plan-step-head'>"
                f"步骤 {i}</div><pre class='plan-pre'>{esc_html(str(step))}</pre></div>"
            )
    return wrap_plan_html("".join(body))


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
        self.btn_open.setObjectName("OpenFileButton")
        self.btn_open.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_path)
        bar_l.addWidget(caption)
        bar_l.addStretch(1)
        bar_l.addWidget(self.btn_open)
        layout.addWidget(bar)

        path_row = QHBoxLayout()
        # 标题下方与文件列表下方留白一致
        path_row.setContentsMargins(12, 0, 12, 8)
        self.title = QLabel("未选择文件")
        self.title.setObjectName("SessionTitle")
        self.title.setWordWrap(True)
        self.stats = QLabel("")
        self.stats.setObjectName("StatusLabel")
        path_row.addWidget(self.title, 1)
        path_row.addWidget(self.stats)
        layout.addLayout(path_row)

        # 外框控制上下等距内边距；列表本身无 QSS padding（Windows 上易不对称）
        self.file_list_frame = QFrame()
        self.file_list_frame.setObjectName("ChangeFileListFrame")
        self.file_list_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame_l = QVBoxLayout(self.file_list_frame)
        frame_l.setContentsMargins(6, 6, 6, 6)
        frame_l.setSpacing(0)
        self.file_list = QListWidget()
        self.file_list.setObjectName("ChangeFileList")
        self.file_list.setSpacing(0)
        self.file_list.setUniformItemSizes(True)
        self.file_list.setFrameShape(QFrame.Shape.NoFrame)
        self.file_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.file_list.currentRowChanged.connect(self._on_file_row)
        frame_l.addWidget(self.file_list)
        self.file_list_frame.hide()
        list_pad = QVBoxLayout()
        list_pad.setContentsMargins(12, 0, 12, 8)
        list_pad.addWidget(self.file_list_frame)
        layout.addLayout(list_pad)

        self.view = QTextBrowser()
        self.view.setObjectName("InspectorView")
        self.view.setOpenExternalLinks(False)
        self.view.document().setDocumentMargin(0)
        font = QFont(pick_ui_mono_font())
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setPointSize(10)
        self.view.setFont(font)
        pad = QVBoxLayout()
        pad.setContentsMargins(12, 0, 12, 12)
        pad.addWidget(self.view, 1)
        layout.addLayout(pad, 1)

        self._path: str | None = None
        self._workdir = ""
        self._group: list[dict[str, Any]] = []
        self._updating_list = False

    def _set_view_html(self, html: str, *, margin: int = 0) -> None:
        """四周留白用 viewportMargins（Qt 富文本几乎忽略 CSS padding）。"""
        m = max(0, int(margin))
        self.view.setViewportMargins(m, m, m, m)
        self.view.document().setDocumentMargin(0)
        self.view.setHtml(html)
        if "diff-line" in html:
            apply_full_width_diff_backgrounds(self.view.document())

    def clear(self) -> None:
        self.title.setText("未选择文件")
        self.stats.setText("")
        self.view.clear()
        self._path = None
        self.btn_open.setEnabled(False)
        self._group = []
        self._updating_list = True
        self.file_list.clear()
        self._updating_list = False
        self.file_list.setFixedHeight(0)
        self.file_list_frame.hide()

    def show_text(self, title: str, text: str, *, path: str | None = None) -> None:
        self.file_list_frame.hide()
        self._group = []
        self.title.setText(title)
        self.stats.setText("")
        esc = (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        self._set_view_html(
            "<html><body style=\"font-family:'Cascadia Code','Cascadia Mono',"
            "Consolas,'Microsoft YaHei UI',monospace;color:#1A1A1A\">"
            f"{esc}</body></html>",
            margin=14,
        )
        self._path = path
        self.btn_open.setEnabled(bool(path))

    def show_change(
        self,
        change: dict[str, Any],
        *,
        group: list[dict[str, Any]] | None = None,
        workdir: str = "",
    ) -> None:
        self._workdir = workdir or ""
        items = [c for c in (group or [change]) if isinstance(c, dict)]
        if not items:
            items = [change] if isinstance(change, dict) else []
        self._group = items
        self._fill_file_list(change)
        self._paint_change(change)

    def show_plan(self, plan: dict[str, Any]) -> None:
        self.file_list_frame.hide()
        self._group = []
        self.title.setText("计划细节")
        steps = [s for s in (plan.get("steps") or []) if isinstance(s, dict)]
        n = len(steps)
        self.stats.setText(f"{n} 步" if n else "")
        self._set_view_html(
            format_plan_html(plan if isinstance(plan, dict) else {}),
            margin=14,
        )
        # 若计划只改一个文件，允许「打开文件」
        paths = []
        for step in steps:
            args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
            p = str(args.get("path") or "").strip()
            if p and p not in paths:
                paths.append(p)
        if len(paths) == 1 and self._workdir:
            self._path = str((Path(self._workdir) / paths[0]).resolve())
        elif len(paths) == 1:
            self._path = paths[0]
        else:
            self._path = None
        self.btn_open.setEnabled(bool(self._path))

    def _fill_file_list(self, selected: dict[str, Any]) -> None:
        self._updating_list = True
        self.file_list.clear()
        sel_path = _rel_path(str(selected.get("path") or ""))
        sel_row = 0
        for i, ch in enumerate(self._group):
            path = str(ch.get("path") or "?")
            name = path.replace("\\", "/").split("/")[-1] or path
            plus, minus = diff_line_stats(str(ch.get("diff") or ""))
            bits = [name]
            if plus:
                bits.append(f"+{plus}")
            if minus:
                bits.append(f"-{minus}")
            item = QListWidgetItem("  ".join(bits))
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setToolTip(path)
            self.file_list.addItem(item)
            if _rel_path(path) == sel_path:
                sel_row = i
        self.file_list.setCurrentRow(sel_row)
        self.file_list_frame.setVisible(bool(self._group))
        self._updating_list = False
        QTimer.singleShot(0, self._fit_file_list_height)

    def _fit_file_list_height(self) -> None:
        n = self.file_list.count()
        if n <= 0:
            self.file_list_frame.hide()
            return
        row_h = self.file_list.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 28
        # 列表高度只包行；上下各 6px 由 file_list_frame 的 layout margin 提供
        h = row_h * n
        h = min(max(h, row_h), 148)
        self.file_list.setFixedHeight(h)

    def _on_file_row(self, row: int) -> None:
        if self._updating_list or row < 0 or row >= len(self._group):
            return
        self._paint_change(self._group[row])

    def _paint_change(self, change: dict[str, Any]) -> None:
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
        if path and self._workdir and not Path(path).is_absolute():
            abs_path = str((Path(self._workdir) / path).resolve())
        name = path.replace("\\", "/").split("/")[-1] or path or "未命名"
        plus, minus = diff_line_stats(diff)
        self.title.setText(name)
        self.title.setToolTip(path)
        stats = []
        if plus:
            stats.append(f"+{plus}")
        if minus:
            stats.append(f"-{minus}")
        self.stats.setText("  ".join(stats))
        self._set_view_html(wrap_diff_html(diff), margin=0)
        self._path = abs_path or None
        self.btn_open.setEnabled(bool(self._path))

    def _open_path(self) -> None:
        if not self._path:
            return
        p = Path(self._path)
        if p.is_file() or p.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            return
        url = QUrl(f"vscode://file/{self._path.replace(chr(92), '/')}")
        QDesktopServices.openUrl(url)
