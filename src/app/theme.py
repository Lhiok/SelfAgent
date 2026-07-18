"""桌面主题：参照 Cursor Agents 浅色三栏窗口。"""

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
  font-size: 13.5px;
  color: #1A1A1A;
  margin: 0;
  padding: 16px 28px 32px 28px;
  background: #F7F7F5;
  line-height: 1.55;
}
.msg {
  margin: 0 0 12px 0;
  max-width: 44rem;
}
.role { display: none; }
.bubble {
  color: #1A1A1A;
}
/* 用户圆角气泡底图（table 单元格，Qt 可渲染圆角） */
table.user-wrap {
  margin: 0 0 14px 0;
  border-collapse: separate;
  max-width: 85%;
}
td.user-bubble {
  background-color: #E8E8E5;
  color: #1A1A1A;
  border-radius: 18px;
  -qt-border-radius: 18px;
  padding: 12px 16px;
}
.assistant .bubble {
  background: transparent;
  padding: 2px 0;
  display: block;
}
.error .bubble {
  background: #FCEBEB;
  border-radius: 14px;
  padding: 10px 14px;
  color: #A33;
  display: inline-block;
}
.meta {
  color: #8A8A8A;
  font-size: 12px;
  margin: 4px 0 10px 0;
}
.meta a {
  color: #3B6EF5;
  text-decoration: none;
}
.meta a:hover { text-decoration: underline; }
.live {
  margin: 0 0 16px 0;
  padding: 0 0 0 12px;
  border-left: 2px solid #C8C8C4;
  max-width: 44rem;
}
.live .role {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #8A8A8A;
  margin-bottom: 6px;
}
.step {
  margin: 6px 0;
  padding: 8px 10px;
  background: #FFFFFF;
  border: 1px solid #EBEBE8;
  border-radius: 10px;
}
.step-title {
  font-weight: 600;
  color: #6B6B6B;
  font-size: 11px;
}
.thought {
  color: #8A8A8A;
  font-style: italic;
  margin-top: 4px;
  font-size: 12px;
}
.chip {
  display: inline-block;
  background: #F0F0EE;
  color: #4A4A4A;
  border-radius: 6px;
  padding: 2px 8px;
  margin: 4px 4px 0 0;
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 11px;
}
.changes {
  margin: 10px 0 14px 0;
  padding: 0;
  background: transparent;
  border: none;
  max-width: 44rem;
}
.changes-head {
  font-size: 12px;
  font-weight: 600;
  color: #6B6B6B;
  margin: 0 0 8px 0;
}
.change-row {
  display: block;
  padding: 8px 12px;
  margin: 0 0 4px 0;
  background: #FFFFFF;
  border: 1px solid #EBEBE8;
  border-radius: 10px;
  text-decoration: none;
  color: #1A1A1A;
}
.change-row:hover {
  background: #F5F5F3;
  border-color: #E0E0DC;
}
.change-path {
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
}
.plus { color: #2E7D4F; font-weight: 600; margin-left: 8px; font-size: 12px; }
.minus { color: #C44; font-weight: 600; margin-left: 6px; font-size: 12px; }
.plan-box {
  margin: 10px 0 14px 0;
  padding: 12px 14px;
  background: #FFF9EB;
  border: 1px solid #F0E2B8;
  border-radius: 12px;
  max-width: 44rem;
}
.empty {
  color: #8A8A8A;
  margin-top: 72px;
  font-size: 13px;
  line-height: 1.7;
  max-width: 26rem;
}
.empty b {
  color: #1A1A1A;
  font-weight: 600;
  display: block;
  margin-bottom: 8px;
  font-size: 16px;
}
"""

DIFF_DOC_CSS = """
body {
  font-family: 'Cascadia Mono', Consolas, monospace;
  font-size: 12px;
  margin: 0;
  padding: 8px 0;
  background: #FFFFFF;
  color: #1A1A1A;
  line-height: 1.45;
}
.line { white-space: pre; padding: 0 12px; }
.add { background: #E8F8EE; color: #1A4D2E; }
.del { background: #FCEBEB; color: #7A2E2E; }
.hunk { background: #F5F5F3; color: #6B6B6B; }
.meta { color: #8A8A8A; }
"""


def wrap_chat_html(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CHAT_DOC_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def wrap_diff_html(diff: str) -> str:
    rows: list[str] = []
    for raw in (diff or "").splitlines():
        esc = (
            raw.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "meta"
        elif raw.startswith("@@"):
            cls = "hunk"
        elif raw.startswith("+"):
            cls = "add"
        elif raw.startswith("-"):
            cls = "del"
        else:
            cls = ""
        rows.append(f"<div class='line {cls}'>{esc or '&nbsp;'}</div>")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{DIFF_DOC_CSS}</style></head>"
        f"<body>{''.join(rows)}</body></html>"
    )


def diff_line_stats(diff: str) -> tuple[int, int]:
    plus = minus = 0
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    return plus, minus
