"""对话上下文压缩。"""

from __future__ import annotations

from typing import Any

from ai import AIClient, AIMessage
from log import get_logger

logger = get_logger("react.compaction")


def _message_text(messages: list[AIMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.role
        content = (m.content or "").strip()
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1199] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def hard_trim(
    messages: list[AIMessage],
    *,
    keep_recent: int,
) -> list[AIMessage]:
    """保留最近 keep_recent 条非 system 消息。"""
    system: list[AIMessage] = []
    rest: list[AIMessage] = []
    for m in messages:
        if m.role == "system" and not rest:
            system.append(m)
        else:
            rest.append(m)
    if len(rest) <= keep_recent:
        return list(messages)
    trimmed = rest[-keep_recent:]
    return [*system, *trimmed]


def compact_messages(
    messages: list[AIMessage],
    *,
    ai: AIClient | None = None,
    compact_after: int = 32,
    keep_recent: int = 12,
) -> list[AIMessage]:
    """
    当非 system 消息超过 compact_after 时压缩：
    - 有 AI：用模型摘要旧消息
    - 无 AI：硬截断，仅保留最近 keep_recent
    """
    if compact_after <= 0:
        return messages

    system: list[AIMessage] = []
    rest: list[AIMessage] = []
    for m in messages:
        if m.role == "system" and not rest:
            system.append(m)
        else:
            rest.append(m)

    if len(rest) <= compact_after:
        return messages

    keep = max(1, int(keep_recent))
    if len(rest) <= keep:
        return messages

    old = rest[:-keep]
    recent = rest[-keep:]
    summary_text = ""

    if ai is not None:
        try:
            prompt = (
                "请将以下对话历史压缩为简洁中文摘要，保留任务目标、已确认约束、"
                "关键路径/结论与未完成事项。不要输出工具调用格式。\n\n"
                f"{_message_text(old)}"
            )
            summary_text = (ai.ask(prompt) or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"上下文摘要失败，回退硬截断: {exc}")
            summary_text = ""

    if not summary_text:
        summary_text = (
            f"（已省略较早的 {len(old)} 条消息；保留最近 {len(recent)} 条。）"
        )

    summary_msg = AIMessage(
        role="user",
        content=f"[上下文摘要]\n{summary_text}",
    )
    logger.notice(
        f"上下文压缩：{len(old)} 条 → 摘要，保留最近 {len(recent)} 条"
    )
    return [*system, summary_msg, *recent]


def compaction_settings(conv_cfg: dict[str, Any] | None) -> tuple[int, int, bool]:
    """返回 (compact_after, keep_recent, use_ai)。热路径默认不用 AI 摘要，避免挡住 done。"""
    cfg = conv_cfg or {}
    after = int(cfg.get("compact_after_messages", 0) or 0)
    keep = int(cfg.get("compact_keep_recent", 12) or 12)
    use_ai = bool(cfg.get("compact_use_ai", False))
    return after, keep, use_ai
