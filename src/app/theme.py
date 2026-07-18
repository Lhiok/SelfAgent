"""桌面主题：参照 Cursor Agents 浅色三栏窗口。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

# 圆滑等宽优先；中文回退到系统黑体
UI_MONO_CANDIDATES = (
    "Cascadia Code",
    "Cascadia Mono",
    "Sarasa Mono SC",
    "JetBrains Mono",
    "Consolas",
    "Courier New",
)
UI_FONT_CSS = (
    "'Cascadia Code', 'Cascadia Mono', 'Sarasa Mono SC', "
    "Consolas, 'Microsoft YaHei UI', monospace"
)
UI_FONT_QSS = (
    '"Cascadia Code", "Cascadia Mono", "Sarasa Mono SC", '
    'Consolas, "Microsoft YaHei UI", monospace'
)


def pick_ui_mono_font() -> str:
    families = set(QFontDatabase.families())
    for name in UI_MONO_CANDIDATES:
        if name in families:
            return name
    return "Consolas"


def apply_theme(app: QApplication) -> None:
    qss_path = Path(__file__).with_name("style.qss")
    if qss_path.is_file():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
    font = QFont(pick_ui_mono_font())
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(10)
    app.setFont(font)


# 片段样式（嵌在 QLabel 富文本里；用户气泡用原生 QLabel + QSS，不走这里）
CHAT_FRAG_CSS = """
body {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 13px;
  color: #1A1A1A;
  margin: 0;
  padding: 0;
  background: transparent;
  line-height: 1.15;
}
.msg {
  margin: 0;
  width: 100%;
}
.role { display: none; }
.bubble {
  color: #1A1A1A;
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
  display: block;
}
.meta {
  color: #8A8A8A;
  font-size: 12px;
  margin: 1px 0 2px 0;
}
.meta a {
  color: #3B6EF5;
  text-decoration: none;
}
.meta a:hover { text-decoration: underline; }
.ask-box {
  margin: 0 0 6px 0;
  padding: 8px 10px;
  background: #FFFFFF;
  border: 1px solid #EBEBE8;
  border-radius: 10px;
  width: 100%;
}
.ask-head {
  font-size: 11px;
  font-weight: 600;
  color: #6B6B6B;
  margin: 0 0 4px 0;
}
.ask-row {
  margin: 0 0 4px 0;
  padding: 0 0 4px 0;
  border-bottom: 1px solid #F0F0EE;
}
.ask-row:last-child {
  margin: 0;
  padding: 0;
  border-bottom: none;
}
.ask-q {
  color: #4A4A4A;
  font-size: 12.5px;
  margin: 0 0 4px 0;
}
.ask-a {
  color: #1A1A1A;
  font-size: 13px;
  font-weight: 600;
}
.live {
  margin: 0;
  padding: 0 0 0 12px;
  border-left: 2px solid #C8C8C4;
  width: 100%;
}
.live .role {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #8A8A8A;
  margin-bottom: 2px;
}
.step {
  margin: 2px 0;
  padding: 4px 6px;
  background: #FFFFFF;
  border: 1px solid #EBEBE8;
  border-radius: 8px;
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
  font-family: """ + UI_FONT_CSS + """;
  font-size: 11px;
}
.changes {
  margin: 10px 0 14px 0;
  padding: 0;
  background: transparent;
  border: none;
  width: 100%;
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
  font-family: """ + UI_FONT_CSS + """;
  font-size: 12px;
}
.plus { color: #2E7D4F; font-weight: 600; margin-left: 8px; font-size: 12px; }
.minus { color: #C44; font-weight: 600; margin-left: 6px; font-size: 12px; }
.plan-box {
  margin: 4px 0 6px 0;
  padding: 8px 10px;
  background: #FFF9EB;
  border: 1px solid #F0E2B8;
  border-radius: 10px;
  width: 100%;
}
.empty {
  color: #8A8A8A;
  margin-top: 28px;
  font-size: 13px;
  line-height: 1.6;
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

CHAT_DOC_CSS = CHAT_FRAG_CSS

DIFF_DOC_CSS = """
body {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 12px;
  margin: 0;
  padding: 0;
  background: #FFFFFF;
  color: #1A1A1A;
}
/* 用 p 而非 table：Qt 表格按内容缩宽，色块右侧会断 */
div.diff {
  margin: 0;
  padding: 0;
  width: 100%;
}
p.diff-line {
  margin: 0;
  padding: 2px 10px;
  line-height: 1;
  white-space: pre;
  color: #1A1A1A;
  background: #FFFFFF;
  -qt-block-indent: 0;
}
span.diff-num {
  color: #9A9A96;
}
span.diff-gutter {
  color: #8A8A8A;
}
span.diff-code {
  color: inherit;
}
p.diff-line.add { background: #E8F8EE; color: #1A4D2E; }
p.diff-line.add span.diff-num { color: #5A8A6A; }
p.diff-line.add span.diff-gutter { color: #2E7D4F; }
p.diff-line.del { background: #FCEBEB; color: #7A2E2E; }
p.diff-line.del span.diff-num { color: #A06060; }
p.diff-line.del span.diff-gutter { color: #C44; }
p.diff-line.hunk { background: #F5F5F3; color: #6B6B6B; }
p.diff-line.hunk span.diff-num { color: #6B6B6B; }
p.diff-line.meta { background: #FFFFFF; color: #8A8A8A; }
"""


def wrap_chat_html(body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CHAT_FRAG_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def esc_html(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_diff_table_html(diff: str) -> str:
    """统一 diff → 行块 HTML（可嵌入计划页）。用 p 铺满宽度，避免 Qt 表格缩宽断色。"""
    hunk_re = re.compile(
        r"^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s*@@"
    )
    rows: list[str] = []
    old_ln = 0
    new_ln = 0

    def _row(cls: str, old: str, new: str, gutter: str, code: str) -> str:
        body = esc_html(code) if code else " "
        kind = f" {cls}" if cls else ""
        # 内联 background：Qt 对 class 背景常只铺文字宽，右侧会断
        bg = {
            "add": "#E8F8EE",
            "del": "#FCEBEB",
            "hunk": "#F5F5F3",
        }.get(cls, "")
        style = (
            f" style='margin:0;padding:2px 10px;line-height:1;white-space:pre;"
            f"background-color:{bg};'"
            if bg
            else " style='margin:0;padding:2px 10px;line-height:1;white-space:pre;'"
        )
        if cls in {"meta", "hunk"}:
            return (
                f"<p class='diff-line{kind}'{style}>"
                f"<span class='diff-code'>{body}</span></p>"
            )
        old_s = f"{old:>4}" if old else "    "
        new_s = f"{new:>4}" if new else "    "
        g = gutter if gutter else " "
        return (
            f"<p class='diff-line{kind}'{style}>"
            f"<span class='diff-num'>{esc_html(old_s)}</span> "
            f"<span class='diff-num'>{esc_html(new_s)}</span> "
            f"<span class='diff-gutter'>{esc_html(g)}</span> "
            f"<span class='diff-code'>{body}</span>"
            f"</p>"
        )

    for raw in (diff or "").splitlines():
        if raw.startswith("diff ") or raw.startswith("index "):
            rows.append(_row("meta", "", "", "", raw))
            continue
        if raw.startswith("---") or raw.startswith("+++"):
            rows.append(_row("meta", "", "", "", raw))
            continue
        m = hunk_re.match(raw)
        if m:
            old_ln = int(m.group(1))
            new_ln = int(m.group(2))
            rows.append(_row("hunk", "", "", "", raw))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            rows.append(_row("add", "", str(new_ln), "+", raw[1:] or " "))
            new_ln += 1
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            rows.append(_row("del", str(old_ln), "", "-", raw[1:] or " "))
            old_ln += 1
            continue
        code = raw[1:] if raw.startswith(" ") else raw
        o = str(old_ln) if old_ln else ""
        n = str(new_ln) if new_ln else ""
        if old_ln:
            old_ln += 1
        if new_ln:
            new_ln += 1
        rows.append(_row("", o, n, " ", code or " "))

    if not rows:
        return "<div class='diff'><p class='diff-line'>(无 diff)</p></div>"
    return f"<div class='diff'>{''.join(rows)}</div>"


def wrap_diff_html(diff: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{DIFF_DOC_CSS}</style></head>"
        f"<body>{build_diff_table_html(diff)}</body></html>"
    )


def apply_full_width_diff_backgrounds(document: Any) -> None:
    """把行背景提到 blockFormat，避免 Qt 只按文字宽度着色导致右侧断开。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QBrush, QTextCharFormat, QTextCursor

    block = document.begin()
    while block.isValid():
        color = None
        bf = block.blockFormat()
        brush = bf.background()
        if brush.style() != Qt.BrushStyle.NoBrush:
            color = brush.color()
        if color is None:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    cbrush = frag.charFormat().background()
                    if cbrush.style() != Qt.BrushStyle.NoBrush:
                        color = cbrush.color()
                        break
                it += 1
        if color is not None and color.alpha() > 0:
            cur = QTextCursor(block)
            new_bf = bf
            new_bf.setBackground(color)
            cur.setBlockFormat(new_bf)
            cur.select(QTextCursor.SelectionType.BlockUnderCursor)
            clear = QTextCharFormat()
            clear.setBackground(QBrush(Qt.BrushStyle.NoBrush))
            cur.mergeCharFormat(clear)
        block = block.next()


PLAN_DOC_CSS = DIFF_DOC_CSS + """
body {
  line-height: 1.2;
}
/* 计划内嵌 diff：勿被 body line-height 拉开色块 */
p.diff-line { line-height: 1; }
.plan-summary {
  font-size: 13px;
  font-weight: 600;
  color: #1A1A1A;
  margin: 0 0 10px 0;
  line-height: 1.25;
  font-family: "Microsoft YaHei UI", sans-serif;
}
.plan-step {
  margin: 0 0 10px 0;
  padding: 0 0 8px 0;
  border-bottom: 1px solid #EBEBE8;
}
.plan-step:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
.plan-step-head {
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 12.5px;
  font-weight: 600;
  color: #1A1A1A;
  margin: 0 0 2px 0;
  line-height: 1.2;
}
.plan-step-meta {
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 11px;
  color: #6B6B6B;
  margin: 0 0 4px 0;
  line-height: 1.2;
}
.plan-step-why {
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 12px;
  color: #4A4A4A;
  margin: 0 0 4px 0;
  line-height: 1.25;
}
.plan-path {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 11px;
  color: #3A3A36;
  background: #F5F5F3;
  border-radius: 6px;
  padding: 3px 8px;
  margin: 0 0 4px 0;
  line-height: 1.2;
}
.plan-pre {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 11px;
  line-height: 1.15;
  white-space: pre-wrap;
  background: #FAFAF8;
  border: 1px solid #EBEBE8;
  border-radius: 8px;
  padding: 6px 8px;
  color: #2A2A28;
  margin: 0;
}
.plan-stats { color: #6B6B6B; font-size: 11px; margin: 0 0 4px 0; line-height: 1.2; }
.plan-stats .plus { color: #2E7D4F; font-weight: 600; }
.plan-stats .minus { color: #C44; font-weight: 600; }
.plan-md {
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 12.5px;
  color: #2A2A28;
  line-height: 1.2;
  background: #FAFAF8;
  border: 1px solid #EBEBE8;
  border-radius: 8px;
  padding: 8px 10px;
}
.plan-md p { margin: 0 0 2px 0; line-height: 1.2; }
.plan-md p:last-child { margin-bottom: 0; }
.plan-md h1, .plan-md h2, .plan-md h3, .plan-md h4 {
  margin: 6px 0 2px 0;
  font-weight: 600;
  color: #1A1A1A;
  line-height: 1.15;
}
.plan-md h1:first-child, .plan-md h2:first-child, .plan-md h3:first-child {
  margin-top: 0;
}
.plan-md h1 { font-size: 15px; }
.plan-md h2 { font-size: 13.5px; }
.plan-md h3 { font-size: 12.5px; }
.plan-md ul, .plan-md ol { margin: 2px 0 4px 0; padding-left: 18px; }
.plan-md li { margin: 0; line-height: 1.2; }
.plan-md code, .plan-md span.inline-code {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 12px;
  color: #6B4E2E;
  background: transparent;
}
.plan-md table.codeblock {
  width: 100%;
  margin: 4px 0;
  background: #E8E8E5;
  border-radius: 6px;
}
.plan-md td.codeblock-cell {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 11.5px;
  line-height: 1.15;
  color: #2A2A28;
  background: #E8E8E5;
  padding: 6px 8px;
  white-space: pre-wrap;
  border: none;
}
.plan-md div.codeblock-lang {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 10px;
  font-weight: 600;
  color: #8A8A86;
  margin: 4px 0 0 0;
  line-height: 1.15;
}
.plan-summary.plan-md {
  background: transparent;
  border: none;
  padding: 0;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.25;
}
"""


def wrap_plan_html(body: str) -> str:
    # 四周留白由 Inspector._set_view_html(margin=…) 负责；Qt 不认 body/div padding
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{PLAN_DOC_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def diff_line_stats(diff: str) -> tuple[int, int]:
    plus = minus = 0
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    return plus, minus
