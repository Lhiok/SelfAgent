"""ReAct 过程细节展示：供用户按级别查看每轮 Thought / Action / Observation。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from react.agent import ActionCall, ReActResult, ReActStep

DETAIL_OFF = "off"
DETAIL_SUMMARY = "summary"
DETAIL_FULL = "full"
DETAIL_LEVELS = frozenset({DETAIL_OFF, DETAIL_SUMMARY, DETAIL_FULL})


def parse_detail_level(value: str | None, *, default: str = DETAIL_OFF) -> str:
    text = str(value if value is not None else default).strip().lower()
    if text in {"false", "0", "no", "none", "quiet"}:
        return DETAIL_OFF
    if text in {"true", "1", "yes", "on"}:
        return DETAIL_SUMMARY
    if text not in DETAIL_LEVELS:
        raise ValueError(
            f"react.detail 只能是 off/summary/full，收到: {value!r}"
        )
    return text


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def format_step_detail(
    step: "ReActStep",
    level: str,
    *,
    max_chars: int = 2000,
) -> str:
    """格式化单轮步骤；level=off 返回空串。"""
    level = parse_detail_level(level)
    if level == DETAIL_OFF:
        return ""

    lines = [f"── 第 {step.index} 步 ──"]
    thought = (step.thought or "").strip()
    if thought:
        lines.append(f"Thought: {truncate_text(thought, max_chars)}")

    if step.final_answer is not None and not step.calls:
        lines.append(f"Final Answer: {truncate_text(step.final_answer.strip(), max_chars)}")
        return "\n".join(lines)

    for i, call in enumerate(step.calls, start=1):
        prefix = f"[{i}] " if len(step.calls) > 1 else ""
        status = ""
        if call.ok is True:
            status = " (ok)"
        elif call.ok is False:
            status = " (error)"
        lines.append(f"{prefix}Action: {call.action}{status}")
        if level == DETAIL_FULL:
            inp = (call.action_input or "").strip()
            if inp:
                lines.append(f"{prefix}Action Input: {truncate_text(inp, max_chars)}")
            obs = (call.observation or "").strip()
            if obs:
                lines.append(f"{prefix}Observation: {truncate_text(obs, max_chars)}")
        elif level == DETAIL_SUMMARY:
            inp = (call.action_input or "").strip()
            if inp:
                lines.append(f"{prefix}Action Input: {truncate_text(inp, min(max_chars, 200))}")

    if step.final_answer is not None:
        lines.append(f"Final Answer: {truncate_text(step.final_answer.strip(), max_chars)}")

    return "\n".join(lines)


def format_result_detail(
    result: "ReActResult",
    level: str | None = None,
    *,
    max_chars: int = 2000,
) -> str:
    """格式化整次运行的步骤细节。"""
    level = parse_detail_level(level if level is not None else DETAIL_FULL)
    if level == DETAIL_OFF:
        return ""
    chunks = [
        format_step_detail(step, level, max_chars=max_chars)
        for step in result.steps
    ]
    chunks = [c for c in chunks if c]
    if not chunks:
        return ""
    header = f"===== ReAct 细节 ({level}) ====="
    return header + "\n" + "\n\n".join(chunks)


def format_steps_detail(
    steps: Iterable["ReActStep"],
    level: str,
    *,
    max_chars: int = 2000,
) -> str:
    from react.agent import ReActResult

    return format_result_detail(
        ReActResult(answer="", steps=list(steps)),
        level,
        max_chars=max_chars,
    )
