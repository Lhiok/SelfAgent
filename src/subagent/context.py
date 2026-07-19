"""子 Agent 上下文：继承父 workdir / 只读 memory 注入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def inherit_context(
    *,
    workdir: Path | str | None,
    memory_text: str = "",
    parent_summary: str = "",
) -> str:
    parts: list[str] = []
    if workdir:
        parts.append(f"工作目录: {workdir}")
    if parent_summary:
        parts.append(f"父任务摘要:\n{parent_summary}")
    if memory_text:
        parts.append(f"[memory_readonly]\n{memory_text}")
    return "\n\n".join(parts)


def summarize_result(answer: str, *, max_chars: int = 4000) -> str:
    text = (answer or "").strip() or "(empty)"
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text
