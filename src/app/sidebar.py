"""工作区 / 会话侧栏。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class Sidebar(QWidget):
    session_selected = Signal(str)
    add_workspace_requested = Signal()
    new_session_requested = Signal(str)
    refresh_requested = Signal()
    session_patch_requested = Signal(str, dict)
    workspace_patch_requested = Signal(str, dict)

    ROLE_KIND = Qt.ItemDataRole.UserRole
    ROLE_ID = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._show_archived = False
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

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.itemClicked.connect(self._on_click)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        layout.addWidget(self.tree, 1)

        self._act_star = QAction("收藏 / 取消收藏", self)
        self._act_archive = QAction("归档 / 取消归档", self)
        self._act_new = QAction("新建会话", self)
        self._act_rename_ws = QAction("重命名工作区", self)
        self._act_collapse = QAction("折叠 / 展开", self)
        for act in (
            self._act_new,
            self._act_star,
            self._act_archive,
            self._act_rename_ws,
            self._act_collapse,
        ):
            self.tree.addAction(act)
        self._act_star.triggered.connect(lambda: self._emit_session_patch("starred"))
        self._act_archive.triggered.connect(lambda: self._emit_session_patch("archived"))
        self._act_new.triggered.connect(self._emit_new_session)
        self._act_rename_ws.triggered.connect(self._emit_rename_ws)
        self._act_collapse.triggered.connect(self._emit_collapse)

    def show_archived(self) -> bool:
        return self._show_archived

    def _on_archive_toggled(self, on: bool) -> None:
        self._show_archived = on
        self.refresh_requested.emit()

    def populate(self, workspaces: list[dict[str, Any]], current_session_id: str | None) -> None:
        self.tree.clear()
        for ws in workspaces:
            ws_item = QTreeWidgetItem([_ws_label(ws)])
            ws_item.setData(0, self.ROLE_KIND, "workspace")
            ws_item.setData(0, self.ROLE_ID, ws["id"])
            font = ws_item.font(0)
            font.setBold(True)
            ws_item.setFont(0, font)
            collapsed = bool(ws.get("collapsed"))
            self.tree.addTopLevelItem(ws_item)
            ws_item.setExpanded(not collapsed)
            for sess in ws.get("sessions") or []:
                if sess.get("archived") and not self._show_archived:
                    continue
                title = str(sess.get("title") or sess.get("preview") or "新对话")
                prefix = ""
                if sess.get("archived"):
                    prefix += "▤ "
                if sess.get("starred"):
                    prefix += "★ "
                s_item = QTreeWidgetItem([prefix + title])
                s_item.setData(0, self.ROLE_KIND, "session")
                s_item.setData(0, self.ROLE_ID, sess["session_id"])
                s_item.setData(0, Qt.ItemDataRole.UserRole + 2, bool(sess.get("starred")))
                s_item.setData(0, Qt.ItemDataRole.UserRole + 3, bool(sess.get("archived")))
                s_item.setToolTip(0, str(sess.get("preview") or ""))
                ws_item.addChild(s_item)
                if current_session_id and sess["session_id"] == current_session_id:
                    self.tree.setCurrentItem(s_item)

    def _on_click(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, self.ROLE_KIND) == "session":
            sid = str(item.data(0, self.ROLE_ID) or "")
            if sid:
                self.session_selected.emit(sid)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, self.ROLE_KIND) == "workspace":
            self._emit_rename_ws()

    def _current(self) -> tuple[str | None, str | None, QTreeWidgetItem | None]:
        item = self.tree.currentItem()
        if item is None:
            return None, None, None
        return (
            str(item.data(0, self.ROLE_KIND) or ""),
            str(item.data(0, self.ROLE_ID) or ""),
            item,
        )

    def _emit_new_session(self) -> None:
        kind, wid, item = self._current()
        if kind == "session" and item is not None and item.parent() is not None:
            wid = str(item.parent().data(0, self.ROLE_ID) or "")
            kind = "workspace"
        if kind == "workspace" and wid:
            self.new_session_requested.emit(wid)

    def _emit_session_patch(self, field: str) -> None:
        kind, sid, item = self._current()
        if kind != "session" or not sid or item is None:
            return
        starred = bool(item.data(0, Qt.ItemDataRole.UserRole + 2))
        archived = bool(item.data(0, Qt.ItemDataRole.UserRole + 3))
        if field == "starred":
            self.session_patch_requested.emit(sid, {"starred": not starred})
        elif field == "archived":
            self.session_patch_requested.emit(sid, {"archived": not archived})

    def _emit_rename_ws(self) -> None:
        kind, wid, item = self._current()
        if kind == "session" and item is not None and item.parent() is not None:
            wid = str(item.parent().data(0, self.ROLE_ID) or "")
            kind = "workspace"
        if kind != "workspace" or not wid:
            return
        title, ok = QInputDialog.getText(self, "重命名工作区", "显示名称")
        if ok and title.strip():
            self.workspace_patch_requested.emit(wid, {"title": title.strip()})

    def _emit_collapse(self) -> None:
        kind, wid, item = self._current()
        if kind == "session" and item is not None:
            item = item.parent()
            if item is not None:
                wid = str(item.data(0, self.ROLE_ID) or "")
                kind = "workspace"
        if kind != "workspace" or not wid or item is None:
            return
        expanded = item.isExpanded()
        item.setExpanded(not expanded)
        self.workspace_patch_requested.emit(wid, {"collapsed": expanded})


def _ws_label(ws: dict[str, Any]) -> str:
    title = str(ws.get("title") or "").strip()
    path = str(ws.get("path") or "")
    n = int(ws.get("session_count") or len(ws.get("sessions") or []))
    name = title or path or ws.get("id", "工作区")
    return f"{name}  ·  {n}"
