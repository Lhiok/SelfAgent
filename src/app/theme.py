"""桌面主题：Cursor / Codex 风格深色客户端。"""

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


# Flat transcript — closer to Cursor Agent / Codex chat (no chat bubbles)
CHAT_DOC_CSS = """
body {
  font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif;
  font-size: 13px;
  color: #CCCCCC;
  margin: 0;
  padding: 12px 20px 28px 20px;
  background: #181818;
  line-height: 1.55;
}
.msg {
  margin: 0 0 18px 0;
  max-width: 52rem;
}
.role {
  font-size: 11px;
  font-weight: 600;
  color: #6E6E6E;
  margin: 0 0 6px 0;
}
.user .role { color: #3794FF; text-transform: none; letter-spacing: 0; }
.assistant .role { color: #89D185; text-transform: none; letter-spacing: 0; }
.error .role { color: #F48771; text-transform: none; letter-spacing: 0; }
.bubble {
  color: #D4D4D4;
  white-space: normal;
}
.user .bubble { color: #E0E0E0; }
.error .bubble { color: #F48771; }
.meta {
  color: #6E6E6E;
  font-size: 12px;
  margin: 6px 0 10px 0;
}
.meta a {
  color: #3794FF;
  text-decoration: none;
}
.meta a:hover { text-decoration: underline; }
.live {
  margin: 0 0 18px 0;
  padding: 10px 0 4px 12px;
  border-left: 2px solid #3794FF;
  max-width: 52rem;
}
.live .role { color: #3794FF; }
.step {
  margin: 8px 0;
  padding: 8px 10px;
  background: #1E1E1E;
  border: 1px solid #2B2B2B;
  border-radius: 5px;
}
.step-title {
  font-weight: 600;
  color: #9D9D9D;
  font-size: 11px;
}
.thought {
  color: #858585;
  font-style: italic;
  margin-top: 4px;
  font-size: 12px;
}
.chip {
  display: inline-block;
  background: #252526;
  color: #B0B0B0;
  border: 1px solid #3C3C3C;
  border-radius: 4px;
  padding: 1px 7px;
  margin: 4px 4px 0 0;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 11px;
}
.changes {
  margin: 8px 0 12px 0;
  padding: 8px 10px;
  background: #1E1E1E;
  border: 1px solid #2B2B2B;
  border-radius: 5px;
  font-size: 12px;
}
.changes a { color: #3794FF; text-decoration: none; }
.changes a:hover { text-decoration: underline; }
.plan-box {
  margin: 10px 0 14px 0;
  padding: 10px 12px;
  background: #1E1E1E;
  border: 1px solid #3C3C3C;
  border-left: 2px solid #CCA700;
  border-radius: 5px;
  color: #D4D4D4;
  max-width: 52rem;
}
.empty {
  color: #6E6E6E;
  text-align: left;
  margin-top: 64px;
  font-size: 13px;
  line-height: 1.7;
  max-width: 28rem;
}
.empty b {
  color: #9D9D9D;
  font-weight: 600;
  display: block;
  margin-bottom: 6px;
  font-size: 14px;
}
"""


def wrap_chat_html(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CHAT_DOC_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
