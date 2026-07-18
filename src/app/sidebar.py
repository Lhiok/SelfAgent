"""工作区 / 会话侧栏（自定义行，避免 QTree 选中小蓝块）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SessionRow(QFrame):
    clicked = Signal(str)
    star_toggled = Signal(str, bool)

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
        self.setProperty("selected", selected)
        self.setProperty("archived", archived)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 6, 4)
        layout.setSpacing(6)

        self.star_btn = QToolButton()
        self.star_btn.setObjectName("StarButton")
        self.star_btn.setAutoRaise(True)
        self.star_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._paint_star()
        self.star_btn.clicked.connect(self._on_star)
        layout.addWidget(self.star_btn)

        self.title = QLabel(title)
        self.title.setObjectName("SessionTitleLabel")
        self.title.setWordWrap(False)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.title, 1)

        if archived:
            tag = QLabel("归档")
            tag.setObjectName("ArchiveTag")
            layout.addWidget(tag)

    def _paint_star(self) -> None:
        if self._starred:
            self.star_btn.setText("★")
            self.star_btn.setProperty("starred", True)
        else:
            self.star_btn.setText("☆")
            self.star_btn.setProperty("starred", False)
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
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class WorkspaceBlock(QWidget):
    new_session = Signal(str)
    rename = Signal(str)
    collapse_changed = Signal(str, bool)
    session_clicked = Signal(str)
    session_star = Signal(str, bool)

    def __init__(self, ws: dict[str, Any], current_session_id: str | None, parent=None) -> None:
        super().__init__(parent)
        self.workspace_id = str(ws["id"])
        self._collapsed = bool(ws.get("collapsed"))
        self.setObjectName("WorkspaceBlock")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(2)

        # —— 仓库行：不进入“选中会话”状态 ——
        header = QFrame()
        header.setObjectName("WorkspaceHeader")
        header.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 6, 4, 6)
        h.setSpacing(8)

        self.chevron = QToolButton()
        self.chevron.setObjectName("ChevronButton")
        self.chevron.setAutoRaise(True)
        self.chevron.setText("▸" if self._collapsed else "▾")
        self.chevron.clicked.connect(self._toggle_collapse)
        h.addWidget(self.chevron)

        icon = QLabel("📁")
        icon.setObjectName("FolderIcon")
        h.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = str(ws.get("title") or "").strip()
        path = str(ws.get("path") or "")
        name = title or Path(path).name or self.workspace_id
        self.name_label = QLabel(name)
        self.name_label.setObjectName("WorkspaceName")
        self.path_label = QLabel(_short_path(path))
        self.path_label.setObjectName("WorkspacePath")
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
        h.addWidget(self.btn_new)

        root.addWidget(header)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        act_rename = QAction("重命名工作区", header)
        act_rename.triggered.connect(lambda: self.rename.emit(self.workspace_id))
        header.addAction(act_rename)

        self.sessions_wrap = QWidget()
        self.sessions_wrap.setObjectName("SessionList")
        self._sess_layout = QVBoxLayout(self.sessions_wrap)
        self._sess_layout.setContentsMargins(22, 0, 0, 0)
        self._sess_layout.setSpacing(1)

        for sess in ws.get("sessions") or []:
            sid = str(sess.get("session_id") or "")
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
            row.setToolTip(str(sess.get("preview") or ""))
            self._sess_layout.addWidget(row)

        self._sess_layout.addStretch(0)
        root.addWidget(self.sessions_wrap)
        self.sessions_wrap.setVisible(not self._collapsed)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self.chevron.setText("▸" if self._collapsed else "▾")
        self.sessions_wrap.setVisible(not self._collapsed)
        self.collapse_changed.emit(self.workspace_id, self._collapsed)


class Sidebar(QWidget):
    session_selected = Signal(str)
    add_workspace_requested = Signal()
    new_session_requested = Signal(str)
    refresh_requested = Signal()
    session_patch_requested = Signal(str, dict)
    workspace_patch_requested = Signal(str, dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._show_archived = False
        self._current_session_id: str | None = None
        self._blocks: list[WorkspaceBlock] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 8, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        caption = QLabel("仓库")
        caption.setObjectName("PanelCaption")
        head.addWidget(caption, 1)
        self.btn_archive = QPushButton("归档")
        self.btn_archive.setObjectName("GhostButton")
        self.btn_archive.setCheckable(True)
        self.btn_archive.setToolTip("显示已归档会话")
        self.btn_archive.toggled.connect(self._on_archive_toggled)
        btn_add = QPushButton("+")
        btn_add.setObjectName("IconButton")
        btn_add.setToolTip("添加工作目录")
        btn_add.clicked.connect(self.add_workspace_requested.emit)
        head.addWidget(self.btn_archive)
        head.addWidget(btn_add)
        layout.addLayout(head)

        scroll = QScrollArea()
        scroll.setObjectName("SidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_host.setObjectName("SidebarList")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_host)
        layout.addWidget(scroll, 1)

    def show_archived(self) -> bool:
        return self._show_archived

    def _on_archive_toggled(self, on: bool) -> None:
        self._show_archived = on
        self.refresh_requested.emit()

    def populate(self, workspaces: list[dict[str, Any]], current_session_id: str | None) -> None:
        self._current_session_id = current_session_id
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._blocks.clear()

        for ws in workspaces:
            data = dict(ws)
            sessions = []
            for sess in ws.get("sessions") or []:
                if sess.get("archived") and not self._show_archived:
                    continue
                sessions.append(sess)
            data["sessions"] = sessions
            block = WorkspaceBlock(data, current_session_id)
            block.new_session.connect(self.new_session_requested.emit)
            block.rename.connect(self._rename_workspace)
            block.collapse_changed.connect(self._on_collapse)
            block.session_clicked.connect(self.session_selected.emit)
            block.session_star.connect(self._on_star)
            self._list_layout.addWidget(block)
            self._blocks.append(block)
        self._list_layout.addStretch(1)

    def _rename_workspace(self, workspace_id: str) -> None:
        title, ok = QInputDialog.getText(self, "重命名工作区", "显示名称")
        if ok and title.strip():
            self.workspace_patch_requested.emit(workspace_id, {"title": title.strip()})

    def _on_collapse(self, workspace_id: str, collapsed: bool) -> None:
        self.workspace_patch_requested.emit(workspace_id, {"collapsed": collapsed})

    def _on_star(self, session_id: str, starred: bool) -> None:
        self.session_patch_requested.emit(session_id, {"starred": starred})


def _short_path(path: str) -> str:
    if not path:
        return ""
    p = path.replace("/", "\\")
    if len(p) <= 36:
        return p
    return "…" + p[-34:]
