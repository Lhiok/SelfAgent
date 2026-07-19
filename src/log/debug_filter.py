"""调试日志分类过滤：--debug=api,hooks 或 --debug=!otel（对齐 claude-code-rev）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebugFilter:
    include: frozenset[str] | None  # None = 全放行（仅受 exclude 约束）
    exclude: frozenset[str]


def parse_debug_filter(spec: str | None) -> DebugFilter | None:
    """解析 `api,hooks` / `!otel,!1p` / `api,!hooks`。空串返回 None（不过滤）。"""
    raw = (spec or "").strip()
    if not raw:
        return None
    include: set[str] = set()
    exclude: set[str] = set()
    has_positive = False
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token.startswith("!"):
            name = token[1:].strip()
            if name:
                exclude.add(name)
        else:
            has_positive = True
            include.add(token)
    return DebugFilter(
        include=frozenset(include) if has_positive else None,
        exclude=frozenset(exclude),
    )


def extract_categories(message: str, *, logger_name: str = "") -> list[str]:
    """从消息与 logger 名提取分类标签。"""
    cats: list[str] = []
    if logger_name:
        cats.append(logger_name.lower())
        # skills.shell_run → skills, shell_run
        for part in logger_name.lower().replace("-", "_").split("."):
            if part:
                cats.append(part)
    text = (message or "").lstrip()
    # [CATEGORY] ...
    if text.startswith("[") and "]" in text:
        inner = text[1 : text.index("]")].strip().lower()
        if inner:
            cats.append(inner)
    # category: ...
    if ":" in text:
        head = text.split(":", 1)[0].strip().lower()
        if head and " " not in head and len(head) < 40:
            cats.append(head)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in cats:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def should_show_debug_message(
    message: str,
    filt: DebugFilter | None,
    *,
    logger_name: str = "",
) -> bool:
    if filt is None:
        return True
    cats = extract_categories(message, logger_name=logger_name)
    if any(c in filt.exclude for c in cats):
        return False
    if filt.include is None:
        return True
    if not cats:
        return False
    return any(c in filt.include for c in cats)
