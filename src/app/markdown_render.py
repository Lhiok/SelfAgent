"""将 Markdown 转为 QTextBrowser 可用的 HTML（代码块做 Qt 兼容处理）。"""

from __future__ import annotations

import re

try:
    import markdown as _md
except ImportError:  # pragma: no cover
    _md = None

from app.theme import UI_FONT_CSS

MD_CSS = """
body {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 13px;
  color: #1A1A1A;
  margin: 0;
  padding: 0;
  background: transparent;
  line-height: 1.15;
}
p { margin: 0 0 1px 0; }
p:last-child { margin-bottom: 0; }
h1, h2, h3, h4 {
  margin: 3px 0 0 0;
  font-weight: 600;
  line-height: 1.15;
}
h1 { font-size: 16px; }
h2 { font-size: 14.5px; }
h3 { font-size: 13.5px; }
ul, ol { margin: 2px 0 4px 0; padding-left: 20px; }
li { margin: 0; }
blockquote {
  margin: 4px 0;
  padding: 2px 0 2px 8px;
  border-left: 3px solid #C8C8C4;
  color: #5A5A5A;
}
/* 行内 code：不用背景色（Qt 会按整行高涂底，看起来像多一行） */
code, span.inline-code {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 12.5px;
  background: transparent;
  color: #6B4E2E;
  border-radius: 0;
  padding: 0;
  margin: 0;
}
/* 文件名：缩小字号，无底色（避免 Qt 整行涂底） */
span.file-chip {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 10.5px;
  font-weight: 600;
  background: transparent;
  color: #4A4A46;
  padding: 0;
  margin: 0;
}
/* 文件提示：再缩一点并加大左缩进，如 (Foo.cs 第 87-90 行) */
span.file-hint {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 11px;
  color: #6A6A66;
  margin-left: 14px;
}
/* Qt 对 pre 支持差：代码块用 table.codeblock；淡黑底与灰回答气泡区分 */
table.codeblock {
  border-collapse: collapse;
  border-spacing: 0;
  width: 100%;
  margin: 4px 0;
  background: #E8E8E5;
}
td.codeblock-cell {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 12px;
  line-height: 1.35;
  color: #3A3A36;
  background: #E8E8E5;
  padding: 8px 10px;
  white-space: pre-wrap;
  border: none;
}
div.codeblock-lang {
  font-family: """ + UI_FONT_CSS + """;
  font-size: 10px;
  font-weight: 600;
  color: #8A8A86;
  margin: 4px 0 0 0;
  padding: 0;
}
a { color: #3B6EF5; text-decoration: none; }
table.md-table {
  border-collapse: collapse;
  margin: 8px 0;
  width: 100%;
}
table.md-table th, table.md-table td {
  border: 1px solid #E0E0DC;
  padding: 4px 8px;
  text-align: left;
}
table.md-table th { background: #F0F0EE; font-weight: 600; }
/* hr 在 chat 里会拆成原生 MdHrLine；此处仅作兜底 */
hr {
  border: none;
  border-top: 1px solid #E0E0DC;
  margin: 8px 0;
  width: 100%;
}
strong { font-weight: 600; }
em { font-style: italic; }
.md-body { margin: 0; }
.ask-box {
  margin: 0;
  padding: 0;
  background: transparent;
  border: none;
}
.ask-head {
  font-size: 11px;
  font-weight: 600;
  color: #6B6B6B;
  margin: 0 0 2px 0;
}
.ask-row {
  margin: 0 0 2px 0;
  padding: 0;
  border: none;
}
.ask-row:last-child {
  margin: 0;
}
.ask-q {
  color: #4A4A4A;
  font-size: 12.5px;
  margin: 0;
  line-height: 1.15;
}
.ask-a {
  color: #2F2F2C;
  font-size: 13px;
  font-weight: 600;
  margin: 0;
  line-height: 1.15;
}
"""

_PRE_RE = re.compile(
    r"<pre(?:\s[^>]*)?>\s*(?:<code(?:\s+class=\"language-([^\"]+)\")?[^>]*>)?"
    r"(.*?)"
    r"(?:</code>)?\s*</pre>",
    re.DOTALL | re.IGNORECASE,
)

_TABLE_RE = re.compile(r"<table(?=[\s>])", re.IGNORECASE)

# 零宽/BOM/软连字符等对展示无意义的字符
_INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]"
)
# 文末孤立的控制符、替换符
_TRAILING_JUNK_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]+$"
)
# Markdown / HTML 末尾空段
_TRAILING_EMPTY_HTML_RE = re.compile(
    r"(?:<p>(?:\s|&nbsp;|<br\s*/?>)*</p>|<br\s*/?>|\s)+$",
    re.IGNORECASE,
)


def sanitize_answer_text(text: str) -> str:
    """去掉回答末尾无效字符，并压缩多余空行。"""
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = _INVISIBLE_RE.sub("", s)
    s = _TRAILING_JUNK_RE.sub("", s)
    # 连续空行最多保留一个空行（即 \n\n）
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.rstrip(" \t\n")


_FENCE_RE = re.compile(r"```([^\n`]*)\r?\n(.*?)```", re.DOTALL)
# 代码块前的最后一个标题（如 ## 启动方式）单独拆出；用贪婪前缀以匹配末尾标题
_TRAILING_HEADING_RE = re.compile(
    r"(?is)(.*)(<h([1-6])(?:\s[^>]*)?>.*?</h\3>)\s*$"
)


def iter_answer_segments(text: str) -> list[tuple[str, ...]]:
    """拆成 ('html', html) / ('code', lang, source)，代码块交由原生圆角控件渲染。"""
    raw = sanitize_answer_text(text or "")
    if not raw:
        return []
    segs: list[tuple[str, ...]] = []
    pos = 0
    for m in _FENCE_RE.finditer(raw):
        before = raw[pos : m.start()]
        if before.strip():
            html = render_markdown(before)
            if html:
                segs.append(("html", html))
        lang = (m.group(1) or "").strip()
        code = m.group(2) or ""
        if code.endswith("\n"):
            code = code[:-1]
        segs.append(("code", lang, code))
        pos = m.end()
    after = raw[pos:]
    if after.strip():
        html = render_markdown(after)
        if html:
            segs.append(("html", html))
    if not segs:
        html = render_markdown(raw)
        if html:
            segs.append(("html", html))
    return _peel_heading_before_code(segs)


def _peel_heading_before_code(segs: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """`</ul><h2>启动方式</h2>` + code → 列表 / 标题 / code，标题紧贴代码块。"""
    out: list[tuple[str, ...]] = []
    for i, seg in enumerate(segs):
        if (
            seg
            and seg[0] == "html"
            and i + 1 < len(segs)
            and segs[i + 1]
            and segs[i + 1][0] == "code"
        ):
            html = seg[1] if len(seg) > 1 else ""
            m = _TRAILING_HEADING_RE.match(html or "")
            if m and m.group(1).strip():
                out.append(("html", m.group(1)))
                out.append(("html", m.group(2).strip()))
                continue
        out.append(seg)
    return out


def render_markdown(text: str) -> str:
    raw = sanitize_answer_text(text or "")
    if not raw:
        return ""
    if _md is None:
        return _trim_trailing_html(_escape_plain(raw))
    html = _md.markdown(
        raw,
        # 不用 nl2br：单换行变 <br> 在等宽字体下会显得行距过大
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html",
    )
    html = _TABLE_RE.sub("<table class='md-table'", html)
    html = _qt_friendly_code_blocks(html)
    html = _restyle_inline_code_and_files(html)
    html = _trim_trailing_html(html)
    # 压缩连续 <br>
    html = re.sub(r"(?:<br\s*/?>\s*){3,}", "<br><br>", html, flags=re.IGNORECASE)
    return html


_FILE_EXT = (
    r"cs|py|js|ts|tsx|jsx|json|java|go|rs|cpp|c|h|hpp|css|html|xml|"
    r"yaml|yml|toml|md|txt|bat|ps1|sh|vue|kt|swift"
)
_FILE_BASENAME_RE = re.compile(
    rf"^(?:[\w.+-]+/)*[\w.+-]+\.(?:{_FILE_EXT})$",
    re.IGNORECASE,
)
_INLINE_CODE_RE = re.compile(r"<code(?:\s[^>]*)?>(.*?)</code>", re.IGNORECASE | re.DOTALL)
_STRONG_FILE_RE = re.compile(
    rf"<strong>\s*((?:[\w.+-]+/)*[\w.+-]+\.(?:{_FILE_EXT}))\s*</strong>",
    re.IGNORECASE,
)
# (`Foo.cs` 第 87-90 行) / Foo.cs 第 87 行
_FILE_HINT_RE = re.compile(
    rf"(?:（|\()?\s*"
    rf"(?:<span class='file-chip'>([^<]+)</span>|([^\s<>()（）]+\.(?:{_FILE_EXT})))"
    rf"\s*(第\s*\d+(?:\s*[-–—~至到]\s*\d+)?\s*行)"
    rf"\s*(?:）|\))?",
    re.IGNORECASE,
)


def _plain_text(html_fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", html_fragment or "").strip()


def _restyle_inline_code_and_files(html: str) -> str:
    """行内 code 去底色；文件名收成 chip；带行号的文件提示加大左缩进。"""

    def code_repl(match: re.Match[str]) -> str:
        inner = match.group(1) or ""
        plain = _plain_text(inner)
        if _FILE_BASENAME_RE.match(plain):
            return f"<span class='file-chip'>{inner}</span>"
        return f"<span class='inline-code'>{inner}</span>"

    out = _INLINE_CODE_RE.sub(code_repl, html or "")
    out = _STRONG_FILE_RE.sub(r"<span class='file-chip'>\1</span>", out)

    def hint_repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2) or ""
        lines = match.group(3) or ""
        return (
            f"<span class='file-hint'>"
            f"<span class='file-chip'>{name}</span> {lines}"
            f"</span>"
        )

    return _FILE_HINT_RE.sub(hint_repl, out)


def _trim_trailing_html(html: str) -> str:
    out = (html or "").rstrip()
    prev = None
    while prev != out:
        prev = out
        out = _TRAILING_EMPTY_HTML_RE.sub("", out).rstrip()
    return out


def wrap_md_html(fragment: str, *, color: str = "#3A3A36") -> str:
    # body 内联颜色，避免富文本忽略 QLabel 样式表
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{MD_CSS}</style></head>"
        f"<body style='color:{color}'>{fragment}</body></html>"
    )


def _qt_friendly_code_blocks(html: str) -> str:
    """把 <pre><code> 转成 table，避免 QTextBrowser 丢掉换行/底色。"""

    def repl(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        inner = match.group(2) or ""
        if inner.endswith("\n"):
            inner = inner[:-1]
        lines_out: list[str] = []
        for line in inner.split("\n"):
            line = line.replace("\t", "    ")
            # 内容已被 markdown 转义；只把行首空格换成 &nbsp; 以保留缩进
            leading = len(line) - len(line.lstrip(" "))
            body = line.lstrip(" ")
            if not body:
                lines_out.append("&nbsp;")
            else:
                lines_out.append(("&nbsp;" * leading) + body)
        joined = "<br>\n".join(lines_out) if lines_out else "&nbsp;"
        lang_html = (
            f"<div class='codeblock-lang'>{_escape_attr(lang)}</div>" if lang else ""
        )
        return (
            f"{lang_html}"
            "<table class='codeblock' width='100%' cellspacing='0' cellpadding='0'>"
            f"<tr><td class='codeblock-cell'>{joined}</td></tr>"
            "</table>"
        )

    return _PRE_RE.sub(repl, html)


def _escape_attr(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _escape_plain(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
