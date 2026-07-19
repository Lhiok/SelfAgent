"""工作区 / 会话侧栏（自定义行，避免 QTree 选中小蓝块）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ElidedLabel(QLabel):
    """单行省略，避免长标题撑破选中底色或裁切成半个字。"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = text or ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        # 先放空串，真正绘制在 paintEvent，避免 resize 里 setText 递归
        super().setText("")

    def set_full_text(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self._full[:24] or "…"), metrics.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        metrics = QFontMetrics(self.font())
        return QSize(0, metrics.height())

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        painter.drawText(
            self.contentsRect(),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )


class SessionRow(QFrame):
    clicked = Signal(str)
    star_toggled = Signal(str, bool)
    archive_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(
        self,
        session_id: str,
        title: str,
        *,
        starred: bool,
        archived: bool,
        selected: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.session_id = session_id
        self._starred = starred
        self.setObjectName("SessionRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "true" if selected else "false")
        self.setProperty("archived", "true" if archived else "false")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(32)
        self.setMinimumWidth(0)

        layout = QHBoxLayout(self)
        # 内容缩进；选中底色仍铺满行宽，与仓库头对齐
        layout.setContentsMargins(18, 0, 8, 0)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.star_btn = QToolButton()
        self.star_btn.setObjectName("StarButton")
        self.star_btn.setAutoRaise(True)
        self.star_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.star_btn.setFixedSize(QSize(20, 20))
        self._paint_star()
        self.star_btn.clicked.connect(self._on_star)
        layout.addWidget(self.star_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title = ElidedLabel(title)
        self.title.setObjectName("SessionTitleLabel")
        self.title.set_full_text(title)
        layout.addWidget(self.title, 1)

        if archived:
            tag = QLabel("归档")
            tag.setObjectName("ArchiveTag")
            layout.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        act_archive = QAction("归档", self)
        act_delete = QAction("删除", self)
        act_archive.triggered.connect(lambda: self.archive_requested.emit(self.session_id))
        act_delete.triggered.connect(lambda: self.delete_requested.emit(self.session_id))
        menu.addAction(act_archive)
        menu.addAction(act_delete)
        menu.exec(self.mapToGlobal(pos))

    def _paint_star(self) -> None:
        if self._starred:
            self.star_btn.setText("★")
            self.star_btn.setProperty("starred", "true")
        else:
            self.star_btn.setText("☆")
            self.star_btn.setProperty("starred", "false")
        self.star_btn.style().unpolish(self.star_btn)
        self.star_btn.style().polish(self.star_btn)

    def _on_star(self) -> None:
        self._starred = not self._starred
        self._paint_star()
        self.star_toggled.emit(self.session_id, self._starred)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            # 点星星不选中行（由按钮处理）
            child = self.childAt(event.position().toPoint())
            if child is self.star_btn or (
                child is not None and self.star_btn.isAncestorOf(child)
            ):
                super().mousePressEvent(event)
                return
            self.clicked.emit(self.session_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class WorkspaceBlock(QWidget):
    new_session = Signal(str)
    rename = Signal(str)
    session_clicked = Signal(str)
    session_star = Signal(str, bool)
    session_archive = Signal(str)
    session_delete = Signal(str)

    def __init__(self, ws: dict[str, Any], current_session_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.workspace_id = str(ws["id"])
        self.setObjectName("WorkspaceBlock")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 8)
        root.setSpacing(2)

        # —— 仓库行：不进入“选中会话”状态 ——
        header = QFrame()
        header.setObjectName("WorkspaceHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        header.setMinimumWidth(0)
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 6, 4, 6)
        h.setSpacing(8)
        h.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        icon = QLabel("📁")
        icon.setObjectName("FolderIcon")
        icon.setFixedWidth(20)
        h.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = str(ws.get("title") or "").strip()
        path = str(ws.get("path") or "")
        name = title or Path(path).name or self.workspace_id
        self.name_label = ElidedLabel(name)
        self.name_label.setObjectName("WorkspaceName")
        self.name_label.set_full_text(name)
        # 展示缩短路径，tooltip 仍是完整路径
        self.path_label = ElidedLabel(_short_path(path))
        self.path_label.setObjectName("WorkspacePath")
        self.path_label.set_full_text(_short_path(path))
        self.path_label.setToolTip(path)
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.path_label)
        h.addLayout(text_col, 1)

        self.btn_new = QToolButton()
        self.btn_new.setObjectName("WorkspacePlus")
        self.btn_new.setAutoRaise(True)
        self.btn_new.setText("+")
        self.btn_new.setToolTip("新建对话")
        self.btn_new.clicked.connect(lambda: self.new_session.emit(self.workspace_id))
        h.addWidget(self.btn_new, 0, Qt.AlignmentFlag.AlignTop)

        root.addWidget(header)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, hdr=header: self._show_workspace_menu(hdr, pos)
        )

        self.sessions_wrap = QWidget()
        self.sessions_wrap.setObjectName("SessionList")
        self.sessions_wrap.setMinimumWidth(0)
        self._sess_layout = QVBoxLayout(self.sessions_wrap)
        # 缩进改由 SessionRow 左边距承担，选中条与仓库同宽
        self._sess_layout.setContentsMargins(0, 0, 0, 0)
        self._sess_layout.setSpacing(2)

        for sess in ws.get("sessions") or []:
            sid = str(sess.get("session_id") or "").strip()
            if not sid:
                continue
            title_s = str(sess.get("title") or sess.get("preview") or "新对话")
            starred = bool(sess.get("starred"))
            archived = bool(sess.get("archived"))
            row = SessionRow(
                sid,
                title_s,
                starred=starred,
                archived=archived,
                selected=bool(current_session_id and sid == current_session_id),
            )
            row.clicked.connect(self.session_clicked.emit)
            row.star_toggled.connect(self.session_star.emit)
            row.archive_requested.connect(self.session_archive.emit)
            row.delete_requested.connect(self.session_delete.emit)
            row.setToolTip(str(sess.get("preview") or title_s))
            self._sess_layout.addWidget(row)

        root.addWidget(self.sessions_wrap)

    def _show_workspace_menu(self, header: QWidget, pos) -> None:
        menu = QMenu(header)
        act_new = QAction("新增对话", header)
        act_rename = QAction("重命名", header)
        act_new.triggered.connect(lambda: self.new_session.emit(self.workspace_id))
        act_rename.triggered.connect(lambda: self.rename.emit(self.workspace_id))
        menu.addAction(act_new)
        menu.addAction(act_rename)
        menu.exec(header.mapToGlobal(pos))


class Sidebar(QWidget):
    session_selected = Signal(str)
    add_workspace_requested = Signal()
    new_session_requested = Signal(str)
    refresh_requested = Signal()
    session_patch_requested = Signal(str, dict)
    workspace_patch_requested = Signal(str, dict)
    session_delete_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._current_session_id: str | None = None
        self._blocks: list[WorkspaceBlock] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 8, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        caption = QLabel("仓库")
        caption.setObjectName("PanelCaption")
        head.addWidget(caption, 1)
        btn_add = QPushButton("+")
        btn_add.setObjectName("IconButton")
        btn_add.setToolTip("添加工作目录")
        btn_add.clicked.connect(self.add_workspace_requested.emit)
        head.addWidget(btn_add)
        layout.addLayout(head)

        scroll = QScrollArea()
        scroll.setObjectName("SidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_host = QWidget()
        self._list_host.setObjectName("SidebarList")
        self._list_host.setMinimumWidth(0)
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_host)
        layout.addWidget(scroll, 1)

    def show_archived(self) -> bool:
        """兼容旧调用：不再提供“显示归档”开关，始终隐藏已归档会话。"""
        return False

    def populate(self, workspaces: list[dict[str, Any]], current_session_id: str | None) -> None:
        self._current_session_id = current_session_id
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._blocks.clear()

        for ws in workspaces:
            data = dict(ws)
            sessions = [
                sess
                for sess in (ws.get("sessions") or [])
                if not sess.get("archived")
            ]
            data["sessions"] = sessions
            block = WorkspaceBlock(data, current_session_id)
            block.new_session.connect(self.new_session_requested.emit)
            block.rename.connect(self._rename_workspace)
            block.session_clicked.connect(self.session_selected.emit)
            block.session_star.connect(self._on_star)
            block.session_archive.connect(self._on_archive)
            block.session_delete.connect(self.session_delete_requested.emit)
            self._list_layout.addWidget(block)
            self._blocks.append(block)
        self._list_layout.addStretch(1)

    def _rename_workspace(self, workspace_id: str) -> None:
        title, ok = QInputDialog.getText(self, "重命名工作区", "显示名称")
        if ok and title.strip():
            self.workspace_patch_requested.emit(workspace_id, {"title": title.strip()})

    def _on_star(self, session_id: str, starred: bool) -> None:
        self.session_patch_requested.emit(session_id, {"starred": starred})

    def _on_archive(self, session_id: str) -> None:
        self.session_patch_requested.emit(session_id, {"archived": True})


def _short_path(path: str) -> str:
    if not path:
        return ""
    p = path.replace("/", "\\")
    if len(p) <= 36:
        return p
    return "…" + p[-34:]
