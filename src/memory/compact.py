"""Transcript 压缩：优先 session notes。"""

from __future__ import annotations

from ai import AIClient, AIMessage
from log import get_logger
from memory.session import SessionMemory

logger = get_logger("memory.compact")

COMPACT_BOUNDARY = "[compact_boundary]"


def hard_trim(
    messages: list[AIMessage],
    *,
    keep_recent: int,
) -> list[AIMessage]:
    system: list[AIMessage] = []
    rest: list[AIMessage] = []
    for m in messages:
        if m.role == "system" and not rest:
            system.append(m)
        else:
            rest.append(m)
    if len(rest) <= keep_recent:
        return list(messages)
    return [*system, *rest[-keep_recent:]]


def compact_transcript(
    messages: list[AIMessage],
    *,
    session: SessionMemory | None = None,
    ai: AIClient | None = None,
    compact_after: int = 32,
    keep_recent: int = 12,
    mark_boundary: bool = True,
) -> list[AIMessage]:
    """
    超过 compact_after 时压缩：
    1) 优先用 session notes 作摘要头
    2) 否则可选 AI 摘要
    3) 否则占位说明 + keep_recent
    """
    if compact_after <= 0:
        return messages

    system: list[AIMessage] = []
    rest: list[AIMessage] = []
    for m in messages:
        if m.role == "system" and COMPACT_BOUNDARY in (m.content or ""):
            continue
        if m.role == "system" and not rest:
            system.append(m)
        else:
            rest.append(m)

    if len(rest) <= compact_after:
        return messages

    keep = max(1, int(keep_recent))
    if len(rest) <= keep:
        return messages

    try:
        from hooks import HookEvent, emit

        emit(
            HookEvent.PRE_COMPACT,
            payload={"message_count": len(rest), "keep_recent": keep},
        )
    except Exception:  # noqa: BLE001
        pass

    old = rest[:-keep]
    recent = rest[-keep:]
    summary_text = ""

    if session is not None:
        try:
            notes = (session.read() or "").strip()
            # 忽略尚未写入实质内容的默认模板
            is_blank_template = (
                "(empty)" in notes
                and "(none)" in notes
                and "### Turn" not in notes
            )
            if notes and not is_blank_template and len(notes) > 40:
                summary_text = notes if len(notes) <= 3500 else notes[:3499] + "…"
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取 session notes 失败: {exc}")

    if not summary_text and ai is not None:
        try:
            prompt = (
                "请将以下对话历史压缩为简洁中文摘要，保留任务目标、已确认约束、"
                "关键路径/结论与未完成事项。不要输出工具调用格式。\n\n"
                f"{_message_text(old)}"
            )
            summary_text = (ai.ask(prompt) or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"上下文摘要失败: {exc}")
            summary_text = ""

    if not summary_text:
        summary_text = (
            f"（已省略较早的 {len(old)} 条消息；保留最近 {len(recent)} 条。）"
        )

    summary_msg = AIMessage(
        role="user",
        content=f"[上下文摘要]\n{summary_text}",
    )
    out = [*system, summary_msg, *recent]
    if mark_boundary:
        out.append(AIMessage(role="system", content=COMPACT_BOUNDARY))
    logger.notice(
        f"memory compact: {len(old)} → summary, keep {len(recent)}"
    )
    try:
        from hooks import HookEvent, emit

        emit(
            HookEvent.POST_COMPACT,
            payload={
                "removed": len(old),
                "kept": len(recent),
                "summary_len": len(summary_text),
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return out


def _message_text(messages: list[AIMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        content = (m.content or "").strip()
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1199] + "…"
        lines.append(f"{m.role}: {content}")
    return "\n".join(lines)
