"""向用户提问并选择方案，用于 Plan Mode 落地前细节确认。"""

from __future__ import annotations

import json
from typing import Any, Callable

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.ask_user")

AskHandler = Callable[[str, list[str], dict[str, Any]], str]


def default_cli_ask(question: str, options: list[str], meta: dict[str, Any]) -> str:
    """命令行交互：打印选项并读取用户输入。"""
    allow_multiple = bool(meta.get("allow_multiple", False))
    allow_custom = bool(meta.get("allow_custom", True))
    default = meta.get("default")

    lines = ["", "======== 需要你确认 ========", question, ""]
    for i, opt in enumerate(options, start=1):
        mark = " (默认)" if default is not None and str(default) in {str(i), opt} else ""
        lines.append(f"  {i}) {opt}{mark}")
    if allow_custom:
        lines.append("  也可直接输入自定义方案文本")
    if allow_multiple:
        lines.append("  多选请用逗号分隔序号，如: 1,3")
    lines.append("请选择后回车:")
    print("\n".join(lines), flush=True)

    try:
        raw = input("> ").strip()
    except EOFError:
        raw = ""

    if not raw and default is not None:
        return str(default)
    return raw


class AskUserSkill(Skill):
    name = "ask_user"
    description = (
        "向用户提问并让其从候选方案中选择（或输入自定义方案）。"
        "适用于 Plan Mode 落地前对实现细节、方案分歧做确认。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "向用户提出的问题",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选方案列表（至少 2 个更有意义）",
            },
            "allow_multiple": {
                "type": "boolean",
                "default": False,
                "description": "是否允许多选",
            },
            "allow_custom": {
                "type": "boolean",
                "default": True,
                "description": "是否允许用户输入不在列表中的自定义方案",
            },
            "default": {
                "description": "默认选项（序号或选项原文），用户直接回车时使用",
            },
            "context": {
                "type": "string",
                "description": "补充背景，帮助用户理解为何要选",
            },
        },
        "required": ["question", "options"],
    }

    def __init__(self, ask_handler: AskHandler | None = None) -> None:
        self.ask_handler = ask_handler or default_cli_ask

    def run(self, **kwargs: Any) -> SkillResult:
        question = str(kwargs.get("question") or "").strip()
        options_raw = kwargs.get("options")
        if not question:
            return SkillResult(ok=False, output="ask_user 需要 question")
        if not isinstance(options_raw, list) or not options_raw:
            return SkillResult(ok=False, output="ask_user 需要非空 options 数组")

        options = [str(o).strip() for o in options_raw if str(o).strip()]
        if len(options) < 1:
            return SkillResult(ok=False, output="options 解析后为空")

        allow_multiple = bool(kwargs.get("allow_multiple", False))
        allow_custom = bool(kwargs.get("allow_custom", True))
        default = kwargs.get("default")
        context = str(kwargs.get("context") or "").strip()
        display_question = f"{question}\n\n背景: {context}" if context else question

        meta = {
            "allow_multiple": allow_multiple,
            "allow_custom": allow_custom,
            "default": default,
            "context": context,
        }

        logger.notice(f"向用户提问: {question[:80]}")
        try:
            raw = self.ask_handler(display_question, options, meta)
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"ask_user 交互失败: {exc}")
            return SkillResult(ok=False, output=f"向用户提问失败: {exc}")

        selected, indexes, err = _resolve_selection(
            str(raw or ""),
            options,
            allow_multiple=allow_multiple,
            allow_custom=allow_custom,
            default=default,
        )
        if err:
            return SkillResult(ok=False, output=err)

        payload = {
            "question": question,
            "options": options,
            "selected": selected,
            "indexes": indexes,
            "raw": str(raw or ""),
            "allow_multiple": allow_multiple,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.notice(f"用户选择: {selected}")
        return SkillResult(ok=True, output=text, data=payload)


def _resolve_selection(
    raw: str,
    options: list[str],
    *,
    allow_multiple: bool,
    allow_custom: bool,
    default: Any,
) -> tuple[list[str], list[int], str | None]:
    text = raw.strip()
    if not text and default is not None:
        text = str(default).strip()
    if not text:
        return [], [], "未收到用户选择"

    parts = [p.strip() for p in text.split(",")] if allow_multiple else [text]
    selected: list[str] = []
    indexes: list[int] = []

    for part in parts:
        if not part:
            continue
        # 序号
        if part.isdigit():
            idx = int(part)
            if 1 <= idx <= len(options):
                selected.append(options[idx - 1])
                indexes.append(idx)
                continue
            return [], [], f"无效序号: {part}（有效 1-{len(options)}）"

        # 精确匹配选项
        matched = next((o for o in options if o == part), None)
        if matched is None:
            # 忽略大小写匹配
            matched = next((o for o in options if o.lower() == part.lower()), None)
        if matched is not None:
            selected.append(matched)
            indexes.append(options.index(matched) + 1)
            continue

        if allow_custom:
            selected.append(part)
            indexes.append(0)
            continue
        return [], [], f"无效选项: {part}；请从列表中选择"

    if not selected:
        return [], [], "未解析到有效选择"
    if not allow_multiple and len(selected) > 1:
        return [], [], "当前不允许选择多个方案"

    # 去重但保序
    uniq_selected: list[str] = []
    uniq_indexes: list[int] = []
    seen: set[str] = set()
    for s, i in zip(selected, indexes):
        if s in seen:
            continue
        seen.add(s)
        uniq_selected.append(s)
        uniq_indexes.append(i)
    return uniq_selected, uniq_indexes, None
