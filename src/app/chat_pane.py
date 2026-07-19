"""中间聊天区：历史、直播步骤、todo、composer。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QProcess, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QPainterPath,
    QShortcut,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Qt 富文本里的 <hr> 会把 QLabel 最小宽度撑死，改用原生分隔线
_HR_SPLIT_RE = re.compile(r"<hr\s*/?>", re.IGNORECASE)

_BUBBLE_COLORS = {
    "UserBubble": QColor("#D6EFE0"),
    # 回答块无底图
    "AssistantBubble": QColor(0, 0, 0, 0),
    "ErrorBubble": QColor("#FCEBEB"),
    # 与原先回答块同色灰底
    "MdCodeBlock": QColor("#E8E8E5"),
}


class RoundedBubble(QFrame):
    """自绘圆角底，避免 Windows 上 QSS border-radius 仍露出方底。"""

    def __init__(
        self,
        object_name: str,
        *,
        radius: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._radius = radius
        self._bg = _BUBBLE_COLORS.get(object_name, QColor("#E8E8E5"))
        # 半透明祖先会导致内部按钮在 Windows 上几乎不绘制；
        # 代码块关闭透明，保证「复制 / 运行」可见。
        if object_name == "MdCodeBlock":
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setAutoFillBackground(False)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAutoFillBackground(False)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._bg.alpha() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bg)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.drawRoundedRect(rect, self._radius, self._radius)


def _resolve_run_spec(lang: str, code: str) -> tuple[list[str], str, str] | None:
    """返回 (argv, temp_path_or_empty, cleanup_hint)；不支持则 None。"""
    key = (lang or "").strip().lower()
    aliases = {
        "py": "python",
        "python3": "python",
        "js": "javascript",
        "node": "javascript",
        "ts": "typescript",
        "sh": "bash",
        "shell": "bash",
        "zsh": "bash",
        "ps": "powershell",
        "ps1": "powershell",
        "pwsh": "powershell",
        "cmd": "batch",
        "bat": "batch",
        "csharp": "cs",
        "c#": "cs",
    }
    key = aliases.get(key, key)
    text = code if code.endswith("\n") else code + "\n"

    def _tmp(suffix: str) -> str:
        fd, path = tempfile.mkstemp(prefix="selfagent_code_", suffix=suffix)
        os.close(fd)
        Path(path).write_text(text, encoding="utf-8")
        return path

    if key in {"", "text", "plain", "markdown", "md", "json", "yaml", "yml", "toml", "xml", "html", "css"}:
        return None
    if key == "python":
        exe = shutil.which("python") or shutil.which("py") or shutil.which("python3")
        if not exe:
            return None
        path = _tmp(".py")
        return [exe, path], path, ""
    if key == "javascript":
        exe = shutil.which("node")
        if not exe:
            return None
        path = _tmp(".js")
        return [exe, path], path, ""
    if key == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if not exe:
            return None
        path = _tmp(".ps1")
        return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path], path, ""
    if key == "batch":
        path = _tmp(".cmd")
        return ["cmd", "/c", path], path, ""
    if key == "bash":
        exe = shutil.which("bash") or shutil.which("sh")
        if not exe:
            return None
        path = _tmp(".sh")
        return [exe, path], path, ""
    # 未知语言：不臆测执行
    return None


class AdaptiveRichLabel(QLabel):
    """可随父级变窄的富文本标签（用 QTextDocument 算高，避免 QLabel 估高虚高）。"""

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, self.fontMetrics().height())

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def _doc_height(self, width: int) -> int:
        # 略收窄宽度再算高，避免实际换行比估算多时把下一块叠上来
        w = max(int(width) - 8, 40)
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        if self.textFormat() == Qt.TextFormat.RichText:
            doc.setHtml(self.text())
        else:
            doc.setPlainText(self.text())
        doc.setTextWidth(w)
        # 仅留很小余量防重叠，避免标题与代码块之间空一大截
        return max(int(doc.size().height()) + 2, self.fontMetrics().height())

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._doc_height(width if width > 1 else max(self.width(), 240))

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.width() if self.width() > 1 else 360
        return QSize(w, self._doc_height(w))

    def sync_height(self, width: int | None = None) -> None:
        """按宽度设置最小高度（可再被布局拉高，避免估矮重叠）。"""
        w = int(width) if width is not None and width > 1 else self.width()
        if w <= 1:
            return
        h = self._doc_height(w)
        self.setMinimumHeight(h)
        self.setMaximumHeight(16777215)


class HrLine(QFrame):
    """回答内分割线：宽度跟随布局，不参与最小宽度计算。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MdHrLine")
        self.setFixedHeight(1)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 1)


class CodeActionChip(QLabel):
    """用 QLabel 自绘操作钮，避免半透明气泡内 QPushButton 不显示。"""

    clicked = Signal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("MdCodeAction")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(18)
        self.setMinimumWidth(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._enabled = True
        self._apply_style()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._enabled = bool(enabled)
        super().setEnabled(enabled)
        self._apply_style()
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self._enabled
            else Qt.CursorShape.ArrowCursor
        )

    def _apply_style(self) -> None:
        if self._enabled:
            self.setStyleSheet(
                "QLabel#MdCodeAction{"
                "background:#D4D4CE;color:#3A3A36;border:1px solid #C8C8C2;"
                "border-radius:4px;padding:0 8px;font-size:10px;}"
            )
        else:
            self.setStyleSheet(
                "QLabel#MdCodeAction{"
                "background:#E0E0DC;color:#A0A09A;border:1px solid #D4D4CE;"
                "border-radius:4px;padding:0 8px;font-size:10px;}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CodeBlockWidget(RoundedBubble):
    """Markdown 代码块：复制 / 运行。"""

    _HEAD_BG = QColor("#D8D8D2")
    _BODY_BG = QColor("#E8E8E5")

    def __init__(
        self,
        lang: str,
        code: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("MdCodeBlock", radius=12, parent=parent)
        self._lang = (lang or "").strip()
        self._code = code or ""
        self._proc: QProcess | None = None
        self._tmp_path = ""
        self._bg = self._BODY_BG
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # 顶栏略深，与下方内容底区分（底色在 paintEvent 里画）
        self._head_bar = QFrame()
        self._head_bar.setObjectName("MdCodeHead")
        self._head_bar.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        head = QHBoxLayout(self._head_bar)
        head.setContentsMargins(8, 4, 8, 4)
        head.setSpacing(4)
        lang_lab = QLabel(self._lang or "code")
        lang_lab.setObjectName("MdCodeLang")
        head.addWidget(lang_lab)
        head.addStretch(1)

        self.btn_copy = CodeActionChip("复制")
        self.btn_copy.clicked.connect(self._copy)
        head.addWidget(self.btn_copy)

        self.btn_run = CodeActionChip("运行")
        self.btn_run.clicked.connect(self._run)
        runnable = _resolve_run_spec(self._lang, self._code) is not None
        self.btn_run.setEnabled(runnable)
        self.btn_run.setToolTip(
            "运行此代码块" if runnable else "当前语言暂不支持直接运行"
        )
        head.addWidget(self.btn_run)
        col.addWidget(self._head_bar)

        body_wrap = QWidget()
        body_wrap.setObjectName("MdCodeBody")
        body_wrap.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body_col = QVBoxLayout(body_wrap)
        body_col.setContentsMargins(8, 5, 8, 5)
        body_col.setSpacing(3)

        # 纯文本算高更稳，避免 <br> 富文本把代码块撑出大块空白
        body = AdaptiveRichLabel(self._code.replace("\t", "    "))
        body.setObjectName("MdCodeText")
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._code_body = body
        body_col.addWidget(body)

        self.output = AdaptiveRichLabel("")
        self.output.setObjectName("MdCodeOutput")
        self.output.setTextFormat(Qt.TextFormat.PlainText)
        self.output.setWordWrap(True)
        self.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.output.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.output.setFixedHeight(0)
        self.output.hide()
        body_col.addWidget(self.output)
        col.addWidget(body_wrap)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)
        painter.fillRect(rect, self._BODY_BG)
        head_h = self._head_bar.height() if self._head_bar is not None else 0
        if head_h > 0:
            painter.fillRect(rect.x(), rect.y(), rect.width(), head_h, self._HEAD_BG)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._code)
        self.btn_copy.setText("已复制")
        self.btn_copy.setMinimumWidth(52)
        QTimer.singleShot(1200, lambda: self.btn_copy.setText("复制"))

    def _run(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
            return
        spec = _resolve_run_spec(self._lang, self._code)
        if spec is None:
            self._show_output("当前语言暂不支持直接运行，可先复制后手动执行。")
            return
        argv, tmp, _ = spec
        self._tmp_path = tmp
        self.btn_run.setText("停止")
        self._show_output("运行中…")
        proc = QProcess(self)
        self._proc = proc
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        prog, *args = argv
        proc.start(prog, args)
        if not proc.waitForStarted(3000):
            self._show_output(f"启动失败：{prog}")
            self._cleanup_run()

    def _on_finished(self, code: int, _status) -> None:
        raw = b""
        if self._proc is not None:
            raw = bytes(self._proc.readAllStandardOutput())
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            text = "(无输出)"
        self._show_output(f"$ exit {code}\n{text}")
        self._cleanup_run()

    def _on_error(self, _err) -> None:
        msg = ""
        if self._proc is not None:
            msg = self._proc.errorString()
        self._show_output(f"运行出错：{msg or 'unknown'}")
        self._cleanup_run()

    def _cleanup_run(self) -> None:
        self.btn_run.setText("运行")
        if self._tmp_path:
            try:
                Path(self._tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._tmp_path = ""
        self._proc = None

    def _show_output(self, text: str) -> None:
        self.output.setText(text or "")
        if text:
            self.output.show()
            self.output.setMaximumHeight(16777215)
            self.output.sync_height(max(self.output.width(), self.width() - 16))
        else:
            self.output.hide()
            self.output.setFixedHeight(0)
        self.updateGeometry()

    def sync_height(self, content_width: int) -> None:
        """按可用宽度重算代码区高度。"""
        inner_w = max(content_width - 16, 40)
        if hasattr(self, "_code_body"):
            self._code_body.sync_height(inner_w)
        if self.output.isVisible() and self.output.text():
            self.output.sync_height(inner_w)
        else:
            self.output.setFixedHeight(0)
        self.updateGeometry()

from app.markdown_render import iter_answer_segments, wrap_md_html
from app.theme import diff_line_stats, wrap_chat_html


def _rel_path(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")


class ChangesBlock(QFrame):
    """对话区改动摘要：仅一行可点，明细在右侧查看。"""

    file_clicked = Signal(dict, list)  # selected, group

    def __init__(
        self,
        changes: list[dict[str, Any]],
        *,
        selected_path: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChangesBlock")
        self._changes = [c for c in changes if isinstance(c, dict)]
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        lay = QHBoxLayout(self)
        # 与回答块左内边距对齐，上下不另加空白以便紧跟回答
        lay.setContentsMargins(16, 0, 0, 0)
        lay.setSpacing(0)
        n = len(self._changes)
        plus = minus = 0
        for ch in self._changes:
            p, m = diff_line_stats(str(ch.get("diff") or ""))
            plus += p
            minus += m
        stats = ""
        if plus:
            stats += f"  +{plus}"
        if minus:
            stats += f"  -{minus}"
        lab = QLabel(f"{n} 个文件已更改{stats}")
        lab.setObjectName("ChangesHead")
        lab.setToolTip("点击在右侧查看改动文件")
        lay.addWidget(lab)
        lay.addStretch(1)
        _ = selected_path  # 选中态由右侧列表承担

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._changes:
            self.file_clicked.emit(self._changes[0], self._changes)
        super().mousePressEvent(event)


class ModePopupItem(QFrame):
    """模式行：左侧图标+文案，右侧选中勾。"""

    clicked = Signal(str)

    def __init__(
        self,
        icon: str,
        label: str,
        mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self.setObjectName("ModePopupItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("checked", False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(6)
        icon_lab = QLabel(icon)
        icon_lab.setObjectName("ModePopupIcon")
        icon_lab.setFixedSize(14, 14)
        icon_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel(label)
        text.setObjectName("ModePopupLabel")
        self.check = QLabel("")
        self.check.setObjectName("ModePopupCheck")
        self.check.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        lay.addWidget(icon_lab, 0)
        lay.addWidget(text, 1)
        lay.addWidget(self.check, 0)

    def set_checked(self, checked: bool) -> None:
        self.setProperty("checked", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.check.setText("✓" if checked else "")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._mode)
        super().mousePressEvent(event)


class ModePopup(QFrame):
    """无圆角透明弹层，避免 QMenu 在 Windows 上露出黑色方底。"""

    mode_picked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setObjectName("ModePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(118)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("ModePopupPanel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(3, 3, 3, 3)
        col.setSpacing(1)

        self.btn_agent = ModePopupItem("∞", "Agent", "agent")
        self.btn_plan = ModePopupItem("☰", "Plan", "plan")
        for item in (self.btn_agent, self.btn_plan):
            item.clicked.connect(self._pick)
            col.addWidget(item)
        outer.addWidget(panel)

    def set_current(self, mode: str) -> None:
        is_plan = mode == "plan"
        self.btn_agent.set_checked(not is_plan)
        self.btn_plan.set_checked(is_plan)

    def _pick(self, mode: str) -> None:
        self.mode_picked.emit(mode)
        self.hide()

    def popup_above(self, anchor: QWidget) -> None:
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, 0))
        self.move(g.x(), g.y() - self.sizeHint().height() - 6)
        self.show()
        self.raise_()
        self.activateWindow()


class ChatStream(QScrollArea):
    """原生消息流：用户气泡用 QLabel+QSS 圆角（避免 QTextBrowser 方块底）。"""

    link_activated = Signal(str)
    change_file_clicked = Signal(dict, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatStream")
        # 手动控制 host 宽高，避免 widgetResizable 把高度撑出可滚动空白
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._selected_change_path: str | None = None

        self._host = QWidget()
        self._host.setObjectName("ChatStreamHost")
        self._host.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self._col = QVBoxLayout(self._host)
        self._col.setContentsMargins(20, 4, 20, 6)
        # 间距按块类型单独插入，见 rebuild
        self._col.setSpacing(0)
        self._col.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._host)
        self._live_label: AdaptiveRichLabel | None = None
        self._reflow_scheduled = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow_host()

    def rebuild(self, parts: list[tuple[str, Any]]) -> None:
        self._live_label = None
        while self._col.count():
            item = self._col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        prev_kind: str | None = None
        for kind, payload in parts:
            if prev_kind is not None:
                if kind == "user":
                    # 新提问前空一行（两段半行）
                    self._col.addSpacing(24)
                elif kind == "changes" and prev_kind in {
                    "assistant",
                    "error",
                    "live",
                }:
                    # 文件更改提示紧跟回答块
                    self._col.addSpacing(2)
                else:
                    self._col.addSpacing(8)
            if kind == "user":
                self._col.addWidget(self._make_plain_bubble(str(payload), "UserBubble"))
            elif kind == "assistant":
                self._col.addWidget(
                    self._make_segment_bubble(payload, "AssistantBubble")
                )
            elif kind == "error":
                self._col.addWidget(self._make_segment_bubble(payload, "ErrorBubble"))
            elif kind == "changes":
                block = ChangesBlock(
                    list(payload or []),
                    selected_path=self._selected_change_path,
                )
                block.file_clicked.connect(self._on_change_file)
                self._col.addWidget(block)
            elif kind == "live":
                lab = self._make_html(str(payload))
                lab.setObjectName("LivePanel")
                self._live_label = lab
                self._col.addWidget(lab)
            else:
                self._col.addWidget(self._make_html(str(payload)))
            prev_kind = kind
        QTimer.singleShot(0, self._after_rebuild)

    def patch_live(self, html: str, *, scroll: bool = False) -> bool:
        """就地更新直播区，避免整页重建造成卡顿/空白闪烁。"""
        lab = self._live_label
        if lab is None:
            return False
        try:
            # 控件可能已 deleteLater
            _ = lab.objectName()
            lab.setText(wrap_chat_html(html))
        except RuntimeError:
            self._live_label = None
            return False
        self._schedule_reflow(scroll=scroll)
        return True

    def _schedule_reflow(self, *, scroll: bool = False) -> None:
        if self._reflow_scheduled:
            if scroll:
                self._pending_scroll = True
            return
        self._reflow_scheduled = True
        self._pending_scroll = scroll

        def _run() -> None:
            self._reflow_scheduled = False
            do_scroll = getattr(self, "_pending_scroll", False)
            self._pending_scroll = False
            self._reflow_host()
            if do_scroll:
                self._scroll_bottom()

        QTimer.singleShot(0, _run)

    def _after_rebuild(self) -> None:
        self._reflow_host()
        self._scroll_bottom()

    def _reflow_host(self) -> None:
        """按视口宽度重算各标签高度，并把 host 收成内容真实高度。"""
        vw = self.viewport().width()
        if vw <= 1:
            return
        for lab in self._host.findChildren(AdaptiveRichLabel):
            lab.setMinimumHeight(0)
            lab.setMaximumHeight(16777215)
        self._host.setFixedWidth(vw)
        # 左右 margin 20*2 + 气泡 padding 16*2
        content_w = max(vw - 40 - 32, 80)
        for block in self._host.findChildren(CodeBlockWidget):
            block.sync_height(content_w)
        for lab in self._host.findChildren(AdaptiveRichLabel):
            if lab.objectName() in {"MdCodeText", "MdCodeOutput"}:
                continue
            lab.sync_height(content_w)
        self._col.activate()
        # 用 heightForWidth 汇总，避免 sizeHint 在固定宽前不准
        hint_h = self._col.heightForWidth(vw)
        if hint_h < 0:
            hint_h = self._col.sizeHint().height()
        self._host.setFixedSize(vw, max(hint_h, 1))

    def _on_change_file(self, change: dict[str, Any], group: list) -> None:
        self._selected_change_path = _rel_path(str(change.get("path") or ""))
        self.change_file_clicked.emit(change, group)

    def _make_plain_bubble(self, text: str, object_name: str) -> QFrame:
        frame = self._bubble_frame(object_name)
        inner = frame.layout()
        assert isinstance(inner, QVBoxLayout)
        esc = (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )
        # 提问略偏深绿，与回答灰字区分
        color = "#17362C" if object_name == "UserBubble" else "#3A3A36"
        lab = AdaptiveRichLabel(
            f'<div style="margin:0;padding:0;line-height:1.15;color:{color}">{esc}</div>'
        )
        lab.setObjectName(f"{object_name}Text")
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setWordWrap(True)
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lab.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(lab)
        return frame

    def _make_segment_bubble(self, segments: Any, object_name: str) -> QFrame:
        """圆角气泡：Markdown 段 + 原生圆角代码块。"""
        frame = self._bubble_frame(object_name)
        inner = frame.layout()
        assert isinstance(inner, QVBoxLayout)
        # 默认间距；标题紧贴代码块时用更小间距（见下）
        inner.setSpacing(0)
        segs = segments if isinstance(segments, list) else [("html", str(segments or ""))]
        prev_was_heading = False
        first = True
        for idx, seg in enumerate(segs):
            if not seg:
                continue
            kind = seg[0]
            next_kind = segs[idx + 1][0] if idx + 1 < len(segs) and segs[idx + 1] else ""
            if kind == "code":
                if not first:
                    # 前文是单独拆出的标题时贴紧，其余与正常段落间距一致
                    inner.addSpacing(2 if prev_was_heading else 4)
                lang = str(seg[1] if len(seg) > 1 else "")
                code = str(seg[2] if len(seg) > 2 else "")
                inner.addWidget(self._make_code_block(lang, code))
                prev_was_heading = False
                first = False
            else:
                html = str(seg[1] if len(seg) > 1 else "")
                if not html.strip():
                    continue
                # <hr> 拆成原生线，避免富文本最小宽度锁死
                parts = _HR_SPLIT_RE.split(html)
                for i, part in enumerate(parts):
                    if not part.strip():
                        if i < len(parts) - 1:
                            if not first:
                                inner.addSpacing(4)
                            inner.addWidget(self._make_hr_line())
                            prev_was_heading = False
                            first = False
                        continue
                    if not first:
                        inner.addSpacing(4)
                    inner.addWidget(self._make_md_view(part, object_name))
                    prev_was_heading = bool(
                        re.search(r"(?is)^\s*<h[1-6][\s>]", part.strip())
                        and next_kind == "code"
                        and i == len(parts) - 1
                    )
                    first = False
                    if i < len(parts) - 1:
                        inner.addSpacing(4)
                        inner.addWidget(self._make_hr_line())
                        prev_was_heading = False
        return frame

    def _make_md_view(self, html_fragment: str, object_name: str) -> QLabel:
        # 用 QLabel 按内容撑高，避免 QTextBrowser 高度多估一行空白
        lab = AdaptiveRichLabel()
        lab.setObjectName(f"{object_name}Text")
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setWordWrap(True)
        lab.setOpenExternalLinks(False)
        lab.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lab.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        lab.linkActivated.connect(self.link_activated.emit)
        color = "#6B3030" if object_name == "ErrorBubble" else "#3A3A36"
        lab.setText(wrap_md_html(html_fragment, color=color))
        return lab

    def _make_hr_line(self) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(0)
        lay.addWidget(HrLine())
        return wrap

    def _make_code_block(self, lang: str, code: str) -> QFrame:
        return CodeBlockWidget(lang, code)

    def _bubble_frame(self, object_name: str) -> QFrame:
        frame = RoundedBubble(object_name, radius=16)
        frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        frame.setMinimumWidth(0)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 5, 16, 5)
        inner.setSpacing(0)
        return frame

    def _make_html(self, fragment: str) -> QLabel:
        lab = AdaptiveRichLabel()
        lab.setObjectName("ChatHtml")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setOpenExternalLinks(False)
        lab.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        lab.linkActivated.connect(self.link_activated.emit)
        lab.setText(wrap_chat_html(fragment))
        lab.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        return lab

    def _scroll_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class ChatPane(QWidget):
    send_requested = Signal(str)
    cancel_requested = Signal()
    enqueue_requested = Signal(str)
    confirm_plan_requested = Signal()
    open_ask_requested = Signal()
    change_clicked = Signal(dict, list)  # selected, group
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
        head_l.setContentsMargins(14, 6, 14, 4)
        self.chat_title = QLabel("未选择会话")
        self.chat_title.setObjectName("SessionTitle")
        head_l.addWidget(self.chat_title, 1)
        layout.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)

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

        self.view = ChatStream()
        self.view.link_activated.connect(self._on_anchor)
        self.view.change_file_clicked.connect(self.change_clicked.emit)
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
        self._mode_popup = ModePopup(self)
        self._mode_popup.mode_picked.connect(self._emit_mode)
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
        # ("user"|"assistant"|"error"|"html"|"live"|"changes", payload)
        self._body_parts: list[tuple[str, Any]] = []
        self._live_steps: list[dict[str, Any]] = []
        self._busy = False
        self._live_status = ""
        self._live_draft = ""
        self._live_draft_step: int | None = None
        self._live_skills: list[dict[str, Any]] = []
        self._live_cancelled = False
        self._live_dirty = False
        self._live_flush_timer = QTimer(self)
        self._live_flush_timer.setSingleShot(True)
        self._live_flush_timer.setInterval(120)
        self._live_flush_timer.timeout.connect(self._flush_live_panel)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        # 运行中仍可输入补充；发送钮变为停止
        self.input.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.btn_confirm.setEnabled(not busy)
        self.btn_mode.setEnabled(not busy)
        if busy:
            self.btn_send.setText("■")
            self.btn_send.setToolTip("停止当前任务（有文字时 Ctrl+Enter 为中途补充）")
            self.input.setPlaceholderText("运行中：输入补充后 Ctrl+Enter；或点 ■ 停止")
        else:
            self.btn_send.setText("↑")
            self.btn_send.setToolTip("发送（Ctrl+Enter）")
            self.input.setPlaceholderText("发送后续消息…")

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_chat_title(self, title: str) -> None:
        self.chat_title.setText(title or "未选择会话")

    def set_mode(self, mode: str) -> None:
        self._mode = "plan" if str(mode or "").lower() == "plan" else "agent"
        self._paint_mode_pill()

    def _paint_mode_pill(self) -> None:
        if self._mode == "plan":
            self.btn_mode.setText("☰ Plan ▾")
        else:
            self.btn_mode.setText("∞ Agent ▾")
        self._mode_popup.set_current(self._mode)

    def _show_mode_menu(self) -> None:
        """在胶囊上方弹出圆角窄菜单（无透明底，无黑方块）。"""
        self._paint_mode_pill()
        self._mode_popup.popup_above(self.btn_mode)

    def _emit_mode(self, mode: str) -> None:
        self._mode = mode
        self._paint_mode_pill()
        self.mode_changed.emit(mode)

    def clear(self) -> None:
        self._body_parts = []
        self._live_steps = []
        self._changes_by_key.clear()
        self._pending_plan = None
        self.view._selected_change_path = None
        self.plan_widget.hide()
        self.ask_widget.hide()
        self.set_todos(None)
        self._paint()

    def render_session(self, session: dict[str, Any]) -> None:
        self.clear()
        parts: list[tuple[str, str]] = []
        for turn in session.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            parts.append(("user", str(turn.get("user") or "")))
            assistant_segs = _compose_assistant_segments(
                str(turn.get("answer") or ""),
                turn.get("ask_answers") or [],
            )
            if assistant_segs:
                parts.append(("assistant", assistant_segs))
            changes = [
                c for c in (turn.get("changes") or []) if isinstance(c, dict)
            ]
            if changes:
                parts.append(("changes", changes))
            detail = turn.get("detail_text") or ""
            if detail:
                key = f"detail-{turn.get('index')}"
                parts.append(
                    ("html", f"<div class='meta'><a href='detail:{key}'>查看过程细节</a></div>")
                )
                self._changes_by_key[key] = {"_detail": detail}
        plan = session.get("pending_plan")
        if isinstance(plan, dict) and plan.get("ok"):
            self._pending_plan = plan
            self.plan_widget.show()
            summary = _escape(
                str(plan.get("summary") or plan.get("text") or "待确认计划")[:240]
            )
            parts.append(
                ("html", f"<div class='plan-box'><b>待确认计划</b><br>{summary}</div>")
            )
        if session.get("pending_ask"):
            self.ask_widget.show()
        self._body_parts = parts
        if not parts:
            self._body_parts = [
                (
                    "html",
                    "<div class='empty'><b>新会话</b>"
                    "在下方输入任务开始。可切换 Agent / Plan，"
                    "过程细节与文件改动在右侧检视。</div>",
                )
            ]
        self.set_todos(session.get("todos"))
        self._paint()

    def begin_live(self, user_text: str) -> None:
        # drop empty placeholder
        if (
            len(self._body_parts) == 1
            and self._body_parts[0][0] == "html"
            and "empty" in self._body_parts[0][1]
        ):
            self._body_parts = []
        self._live_steps = []
        self._live_status = "处理中…"
        self._live_draft = ""
        self._live_draft_step = None
        self._live_skills = []
        self._live_cancelled = False
        self._body_parts.append(("user", user_text))
        self._body_parts.append(
            ("live", "<div class='live'><div class='role'>助手 · 进行中</div></div>")
        )
        # 首次进入直播必须整页构建，以挂上 LivePanel 引用
        self._paint()
        self._flush_live_panel()

    def update_live_status(self, message: str) -> None:
        self._live_status = message or self._live_status
        self._schedule_live_refresh()

    def update_live_steps(self, steps: list[dict[str, Any]]) -> None:
        self._live_steps = steps
        self._live_draft = ""
        self._live_draft_step = None
        self._flush_live_panel()
        all_ch: list[dict[str, Any]] = []
        for step in steps:
            all_ch.extend(c for c in (step.get("changes") or []) if isinstance(c, dict))
        self._set_live_changes(all_ch)

    def append_assistant_delta(self, delta: str, *, step: int | None = None) -> None:
        if not delta:
            return
        if (
            step is not None
            and self._live_draft_step is not None
            and step != self._live_draft_step
        ):
            self._live_draft = ""
        if step is not None:
            self._live_draft_step = step
        self._live_draft = (self._live_draft or "") + delta
        if len(self._live_draft) > 1200:
            self._live_draft = "…" + self._live_draft[-1199:]
        # 思考中不展示草稿，跳过 UI 刷新（流式 token 极多，全量重绘会卡死）
        if "思考" in (self._live_status or ""):
            return
        self._schedule_live_refresh()

    def update_skill_event(self, event: dict[str, Any]) -> None:
        name = str(event.get("skill") or "?")
        st = str(event.get("status") or "start")
        if st == "start":
            self._live_skills.append({"skill": name, "status": "start"})
            if not self._live_cancelled:
                self._live_status = str(event.get("message") or f"执行 {name}…")
        elif st == "end":
            for item in reversed(self._live_skills):
                if item.get("skill") == name and item.get("status") in {
                    "start",
                    "running",
                }:
                    item["status"] = "err" if event.get("ok") is False else "ok"
                    break
            else:
                self._live_skills.append(
                    {
                        "skill": name,
                        "status": "err" if event.get("ok") is False else "ok",
                    }
                )
        if len(self._live_skills) > 12:
            self._live_skills = self._live_skills[-12:]
        self._schedule_live_refresh()

    def mark_cancelled(self, message: str = "") -> None:
        self._live_cancelled = True
        self._live_status = message or "已取消本轮任务"
        self._live_draft = ""
        self._flush_live_panel()

    def finish_live(self, answer: str, *, ok: bool = True) -> None:
        self._body_parts = _drop_live_parts(self._body_parts)
        segs = _compose_assistant_segments(answer, [])
        if segs:
            self._body_parts.append(("assistant" if ok else "error", segs))
        self._live_steps = []
        self._live_draft = ""
        self._live_skills = []
        self._live_cancelled = False
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
        if self._busy:
            if text:
                self.input.clear()
                self.enqueue_requested.emit(text)
                self.set_status(f"已排队补充: {text[:60]}")
            else:
                self.cancel_requested.emit()
            return
        if not text:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def _emit_plan_detail(self) -> None:
        if self._pending_plan:
            self.plan_inspect_requested.emit(self._pending_plan)

    def _on_anchor(self, href: str) -> None:
        if href.startswith("detail:"):
            key = href[len("detail:") :]
            payload = self._changes_by_key.get(key) or {}
            detail = str(payload.get("_detail") or "")
            if detail:
                self.detail_inspect_requested.emit(detail)

    def _set_live_changes(self, changes: list[dict[str, Any]]) -> None:
        new_parts: list[tuple[str, Any]] = []
        i = 0
        parts = self._body_parts
        while i < len(parts):
            kind, payload = parts[i]
            if kind == "live":
                new_parts.append((kind, payload))
                if changes:
                    new_parts.append(("changes", changes))
                if i + 1 < len(parts) and parts[i + 1][0] == "changes":
                    i += 2
                else:
                    i += 1
                continue
            new_parts.append((kind, payload))
            i += 1
        self._body_parts = new_parts
        self._paint()

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

    def _format_live_skills(self) -> str:
        if not self._live_skills:
            return ""
        chips: list[str] = []
        for s in self._live_skills:
            st = str(s.get("status") or "start")
            mark = "✓" if st == "ok" else ("✗" if st == "err" else "…")
            chips.append(
                f"<span class='chip'>{mark} {_escape(str(s.get('skill') or '?'))}</span>"
            )
        return "<div class='meta'>" + "".join(chips) + "</div>"

    def _schedule_live_refresh(self) -> None:
        self._live_dirty = True
        if not self._live_flush_timer.isActive():
            self._live_flush_timer.start()

    def _flush_live_panel(self) -> None:
        self._live_dirty = False
        if self._live_flush_timer.isActive():
            self._live_flush_timer.stop()
        self._refresh_live_panel()

    def _build_live_html(self) -> str:
        role = "助手 · 已取消" if self._live_cancelled else "助手 · 进行中"
        parts = [
            f"<div class='live'><div class='role'>{role}</div>",
            f"<div class='meta'>{_escape(self._live_status or '处理中…')}</div>",
            self._format_live_skills(),
        ]
        # 思考阶段只展示短预览，避免整段 ReAct 原文把中间区撑空/卡顿
        thinking = "思考" in (self._live_status or "")
        if self._live_draft and not self._live_cancelled and not thinking:
            draft = self._live_draft
            if len(draft) > 800:
                draft = "…" + draft[-799:]
            parts.append(
                "<div class='meta'><b>流式输出</b></div>"
                f"<div class='thought'>{_escape(draft)}</div>"
            )
        steps_html = self._format_live_steps()
        parts.append(steps_html or "")
        parts.append("</div>")
        return "".join(parts)

    def _refresh_live_panel(self) -> None:
        live = self._build_live_html()
        for i, (kind, _) in enumerate(self._body_parts):
            if kind == "live":
                self._body_parts[i] = ("live", live)
                break
        else:
            self._body_parts.append(("live", live))
        # 优先就地 patch；失败再整页重建
        if self.view.patch_live(live, scroll=bool(self._live_steps)):
            return
        self._paint()

    def _set_live_inner(self, inner: str, *, role_included: bool = False) -> None:
        if role_included:
            live = inner
        else:
            live = (
                "<div class='live'>"
                "<div class='role'>助手 · 进行中</div>"
                f"{inner}</div>"
            )
        for i, (kind, _) in enumerate(self._body_parts):
            if kind == "live":
                self._body_parts[i] = ("live", live)
                break
        else:
            self._body_parts.append(("live", live))
        if self.view.patch_live(live):
            return
        self._paint()

    def _paint(self) -> None:
        self.view.rebuild(self._body_parts)


def _drop_live_parts(parts: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    i = 0
    while i < len(parts):
        kind, payload = parts[i]
        if kind == "live":
            if i + 1 < len(parts) and parts[i + 1][0] == "changes":
                i += 2
            else:
                i += 1
            continue
        out.append((kind, payload))
        i += 1
    return out


def _compose_assistant_segments(
    answer: str, ask_answers: list[Any]
) -> list[tuple[str, ...]]:
    """Markdown 段 + 代码块段 + 已确认选择，合进同一助手气泡。"""
    segs = list(iter_answer_segments(answer or ""))
    ask = _ask_answers_html(ask_answers or [])
    if ask:
        segs.append(("html", ask))
    return segs


def _ask_answers_html(answers: list[Any]) -> str:
    rows: list[str] = []
    for ans in answers:
        q, selected = _parse_ask_answer(ans)
        if not q and not selected:
            continue
        # 只展示问题首行，避免多段选项把对话高度撑爆
        q_one = (q or "（问题）").strip().splitlines()[0].strip()
        q_html = _escape(q_one)
        sel_html = _escape(selected) if selected else "（未选择）"
        rows.append(
            "<div class='ask-row'>"
            f"<div class='ask-q'>{q_html}</div>"
            f"<div class='ask-a'>已选 · {sel_html}</div>"
            "</div>"
        )
    if not rows:
        return ""
    return (
        "<div class='ask-box'>"
        "<div class='ask-head'>已确认选择</div>"
        f"{''.join(rows)}"
        "</div>"
    )


def _parse_ask_answer(ans: Any) -> tuple[str, str]:
    if isinstance(ans, dict):
        q = str(ans.get("question") or "").strip()
        selected = ans.get("selected")
        if isinstance(selected, list):
            sel = "、".join(str(x).strip() for x in selected if str(x).strip())
        else:
            sel = str(selected or ans.get("raw") or "").strip()
        return q, sel
    text = str(ans or "").strip()
    if not text:
        return "", ""
    # 兜底：偶发被序列化成字面量 dict
    if text.startswith("{") and ("'question'" in text or '"question"' in text):
        try:
            import ast

            data = ast.literal_eval(text)
            if isinstance(data, dict):
                return _parse_ask_answer(data)
        except (SyntaxError, ValueError):
            pass
    return "", text


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
