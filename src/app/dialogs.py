"""与主界面风格一致的轻量弹框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AppMessageDialog(QDialog):
    """信息 / 警告 / 确认弹框（替代系统 QMessageBox）。"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        kind: str = "info",
        yes_no: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel()
        icon.setObjectName("AppDialogIcon")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if kind == "warn":
            icon.setText("!")
            icon.setProperty("kind", "warn")
        elif kind == "question":
            icon.setText("?")
            icon.setProperty("kind", "question")
        else:
            icon.setText("i")
            icon.setProperty("kind", "info")
        icon.style().unpolish(icon)
        icon.style().polish(icon)
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(6)
        title_lab = QLabel(title)
        title_lab.setObjectName("AppDialogTitle")
        title_lab.setWordWrap(True)
        msg_lab = QLabel(message)
        msg_lab.setObjectName("AppDialogMessage")
        msg_lab.setWordWrap(True)
        col.addWidget(title_lab)
        col.addWidget(msg_lab)
        head.addLayout(col, 1)
        root.addLayout(head)

        btns = QHBoxLayout()
        btns.addStretch(1)
        if yes_no:
            cancel = QPushButton("取消")
            cancel.setObjectName("DialogGhostButton")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(self.reject)
            ok = QPushButton("确定")
            ok.setObjectName("DialogPrimaryButton")
            ok.setCursor(Qt.CursorShape.PointingHandCursor)
            ok.clicked.connect(self.accept)
            btns.addWidget(cancel)
            btns.addWidget(ok)
        else:
            ok = QPushButton("知道了")
            ok.setObjectName("DialogPrimaryButton")
            ok.setCursor(Qt.CursorShape.PointingHandCursor)
            ok.clicked.connect(self.accept)
            btns.addWidget(ok)
        root.addLayout(btns)


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    AppMessageDialog(parent, title, message, kind="info").exec()


def show_warning(parent: QWidget | None, title: str, message: str) -> None:
    AppMessageDialog(parent, title, message, kind="warn").exec()


def ask_confirm(parent: QWidget | None, title: str, message: str) -> bool:
    dlg = AppMessageDialog(parent, title, message, kind="question", yes_no=True)
    return dlg.exec() == QDialog.DialogCode.Accepted
