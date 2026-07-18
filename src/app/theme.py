"""桌面主题：加载 QSS 与字体。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def apply_theme(app: QApplication) -> None:
    qss_path = Path(__file__).with_name("style.qss")
    if qss_path.is_file():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    font = QFont("Segoe UI")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(10)
    app.setFont(font)


CHAT_DOC_CSS = """
body {
  font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
  font-size: 13px;
  color: #1A2428;
  margin: 0;
  padding: 8px 12px 24px 12px;
  background: transparent;
  line-height: 1.45;
}
.msg {
  margin: 0 0 14px 0;
  max-width: 92%;
}
.msg.user {
  margin-left: auto;
}
.bubble {
  border-radius: 14px;
  padding: 10px 14px;
  display: block;
}
.user .bubble {
  background: #C5E4E3;
  color: #123335;
  border-top-right-radius: 4px;
}
.assistant .bubble {
  background: #FFFFFF;
  border: 1px solid #D0DBE3;
  border-top-left-radius: 4px;
}
.error .bubble {
  background: #F8EAEA;
  border: 1px solid #D4A0A0;
  color: #7A2E2E;
}
.role {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: #5C6B73;
  margin: 0 0 4px 2px;
}
.user .role { text-align: right; color: #0D7377; }
.meta {
  color: #5C6B73;
  font-size: 12px;
  margin: 6px 0;
}
.live {
  background: #FFFFFF;
  border: 1px dashed #0D7377;
  border-radius: 14px;
  padding: 10px 14px;
  margin-bottom: 14px;
}
.step {
  margin: 8px 0;
  padding: 8px 10px;
  background: #F0F5F6;
  border-radius: 8px;
  border-left: 3px solid #0D7377;
}
.step-title { font-weight: 600; color: #0D7377; font-size: 12px; }
.thought { color: #3D4F57; font-style: italic; margin-top: 4px; }
.chip {
  display: inline-block;
  background: #E2EEF0;
  color: #1A2428;
  border-radius: 6px;
  padding: 2px 8px;
  margin: 3px 4px 0 0;
  font-family: Consolas, monospace;
  font-size: 11px;
}
.changes {
  margin: 8px 0;
  padding: 8px 10px;
  background: #EEF2F5;
  border-radius: 8px;
}
.changes a { color: #0D7377; text-decoration: none; font-weight: 600; }
.changes a:hover { text-decoration: underline; }
.plan-box {
  margin: 10px 0;
  padding: 10px 12px;
  background: #FFF8E8;
  border: 1px solid #E6D19A;
  border-radius: 10px;
}
.empty {
  color: #8A969E;
  text-align: center;
  margin-top: 48px;
  font-size: 14px;
}
"""


def wrap_chat_html(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CHAT_DOC_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
