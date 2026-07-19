"""向用户提问并选择方案，用于 Plan Mode 落地前细节确认。"""

from __future__ import annotations

import json
from typing import Any, Callable

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.ask_user")

AskHandler = Callable[[str, list[str], dict[str, Any]], str]


def default_cli_ask(question: str, options: list[str], meta: dict[str, Any]) -> str:
    """命令行交互：打印选项并读取用户输入。支持 questions 批量。"""
    items = meta.get("questions")
    if isinstance(items, list) and len(items) > 1:
        answers: list[dict[str, str]] = []
        total = len(items)
        for i, item in enumerate(items, start=1):
            print(f"\n======== 问题 {i}/{total} ========", flush=True)
            raw = _cli_prompt_one(
                str(item.get("question") or ""),
                list(item.get("options") or []),
                item,
            )
            answers.append({"id": str(item.get("id") or i), "raw": raw})
        return json.dumps(answers, ensure_ascii=False)

    return _cli_prompt_one(question, options, meta)


def _cli_prompt_one(question: str, options: list[str], meta: dict[str, Any]) -> str:
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
        "若有多个待确认点，请用 questions 数组一次提出，勿拆成多次调用。"
        "options 必须是可读的完整方案文案（例如 "
        "'A. localToWorldMatrix 转四角（推荐）——正确处理旋转'），"
        "禁止只写 A/B/C；方案说明写在 options 里，不要只堆在 question 正文。"
        "适用于 Plan Mode 落地前对实现细节、方案分歧做确认。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "单个问题时的问题文本（与 questions 二选一）",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "选项完整文案列表（含方案要点，勿仅用 A/B/C）",
            },
            "questions": {
                "type": "array",
                "description": "多个问题一次提问（推荐）。每项含 question/options",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "allow_multiple": {"type": "boolean", "default": False},
                        "allow_custom": {"type": "boolean", "default": True},
                        "default": {},
                        "context": {"type": "string"},
                    },
                    "required": ["question", "options"],
                },
            },
            "allow_multiple": {
                "type": "boolean",
                "default": False,
                "description": "单个问题时是否允许多选",
            },
            "allow_custom": {
                "type": "boolean",
                "default": True,
                "description": "单个问题时是否允许自定义方案",
            },
            "default": {
                "description": "单个问题的默认选项（序号或选项原文）",
            },
            "context": {
                "type": "string",
                "description": "单个问题的补充背景",
            },
        },
    }

    def __init__(self, ask_handler: AskHandler | None = None) -> None:
        self.ask_handler = ask_handler or default_cli_ask

    def run(self, **kwargs: Any) -> SkillResult:
        items = coerce_questions(kwargs)
        if not items:
            return SkillResult(
                ok=False,
                output="ask_user 需要 question+options，或非空 questions 数组",
            )

        header = (
            f"请确认以下 {len(items)} 个问题"
            if len(items) > 1
            else str(items[0]["question"])
        )
        display_q = header
        if len(items) == 1 and items[0].get("context"):
            display_q = f"{items[0]['question']}\n\n背景: {items[0]['context']}"

        meta = {
            "batch": len(items) > 1,
            "questions": items,
            "allow_multiple": bool(items[0].get("allow_multiple", False)),
            "allow_custom": bool(items[0].get("allow_custom", True)),
            "default": items[0].get("default"),
            "context": str(items[0].get("context") or ""),
        }
        opts0 = list(items[0].get("options") or [])

        logger.notice(
            f"向用户提问: {len(items)} 题 · {str(items[0].get('question') or '')[:60]}"
        )
        try:
            raw = self.ask_handler(display_q, opts0, meta)
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"ask_user 交互失败: {exc}")
            return SkillResult(ok=False, output=f"向用户提问失败: {exc}")

        answers_raw = _parse_answers_raw(str(raw or ""), len(items))
        results: list[dict[str, Any]] = []
        for item, item_raw in zip(items, answers_raw):
            selected, indexes, err = _resolve_selection(
                item_raw,
                list(item.get("options") or []),
                allow_multiple=bool(item.get("allow_multiple", False)),
                allow_custom=bool(item.get("allow_custom", True)),
                default=item.get("default"),
            )
            if err:
                return SkillResult(
                    ok=False,
                    output=f"问题「{item.get('question')}」: {err}",
                )
            results.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "options": item["options"],
                    "selected": selected,
                    "indexes": indexes,
                    "raw": item_raw,
                    "allow_multiple": bool(item.get("allow_multiple", False)),
                }
            )

        if len(results) == 1:
            payload = results[0]
        else:
            payload = {
                "questions": results,
                "selected": [r["selected"] for r in results],
            }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        logger.notice(f"用户选择: {[r['selected'] for r in results]}")
        return SkillResult(ok=True, output=text, data=payload)


def collect_ask_answers_from_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """从步骤里收集已确认的 ask_user 选择，供对话回顾。"""
    out: list[dict[str, Any]] = []
    for step in steps or []:
        calls = getattr(step, "calls", None) or []
        for call in calls:
            if (getattr(call, "action", "") or "") != "ask_user":
                continue
            if getattr(call, "ok", True) is False:
                continue
            data = getattr(call, "data", None)
            rows = _answers_from_skill_data(data)
            if not rows:
                rows = _answers_from_observation(getattr(call, "observation", None))
            for row in rows:
                if not row.get("question"):
                    continue
                if row.get("selected") is None:
                    continue
                out.append(row)
    return out


def _answers_from_skill_data(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    if data.get("buffered") is not None and "question" not in data:
        return []
    if isinstance(data.get("questions"), list):
        rows: list[dict[str, Any]] = []
        for q in data["questions"]:
            if not isinstance(q, dict):
                continue
            selected = q.get("selected")
            if selected is None:
                continue
            rows.append(
                {
                    "id": str(q.get("id") or ""),
                    "question": str(q.get("question") or "").strip(),
                    "selected": list(selected) if isinstance(selected, list) else [str(selected)],
                    "raw": str(q.get("raw") or ""),
                }
            )
        return rows
    if data.get("question") is not None and data.get("selected") is not None:
        selected = data.get("selected")
        return [
            {
                "id": str(data.get("id") or "1"),
                "question": str(data.get("question") or "").strip(),
                "selected": list(selected) if isinstance(selected, list) else [str(selected)],
                "raw": str(data.get("raw") or ""),
            }
        ]
    return []


def _answers_from_observation(observation: Any) -> list[dict[str, Any]]:
    text = str(observation or "").strip()
    if not text or text.startswith("已暂存") or text.startswith("ERROR"):
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return _answers_from_skill_data(data)


def coerce_questions(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    raw_qs = kwargs.get("questions")
    items: list[dict[str, Any]] = []
    if isinstance(raw_qs, list) and raw_qs:
        for i, q in enumerate(raw_qs, start=1):
            if not isinstance(q, dict):
                continue
            question = str(q.get("question") or "").strip()
            options_raw = q.get("options")
            if not question or not isinstance(options_raw, list):
                continue
            options = [str(o).strip() for o in options_raw if str(o).strip()]
            if not options:
                continue
            qid = str(q.get("id") or i).strip() or str(i)
            items.append(
                {
                    "id": qid,
                    "question": question,
                    "options": options,
                    "allow_multiple": bool(q.get("allow_multiple", False)),
                    "allow_custom": bool(q.get("allow_custom", True)),
                    "default": q.get("default"),
                    "context": str(q.get("context") or "").strip(),
                }
            )
        return items

    question = str(kwargs.get("question") or "").strip()
    options_raw = kwargs.get("options")
    if not question or not isinstance(options_raw, list) or not options_raw:
        return []
    options = [str(o).strip() for o in options_raw if str(o).strip()]
    if not options:
        return []
    return [
        {
            "id": "1",
            "question": question,
            "options": options,
            "allow_multiple": bool(kwargs.get("allow_multiple", False)),
            "allow_custom": bool(kwargs.get("allow_custom", True)),
            "default": kwargs.get("default"),
            "context": str(kwargs.get("context") or "").strip(),
        }
    ]


def _parse_answers_raw(raw: str, n: int) -> list[str]:
    text = raw.strip()
    if n <= 1:
        if text.startswith("["):
            try:
                data = json.loads(text)
                if isinstance(data, list) and data:
                    first = data[0]
                    if isinstance(first, dict):
                        return [str(first.get("raw") or "")]
                    return [str(first)]
            except json.JSONDecodeError:
                pass
        return [text]

    if not text:
        return [""] * n
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [text] + [""] * (n - 1)

    if isinstance(data, list):
        out: list[str] = []
        for i in range(n):
            if i >= len(data):
                out.append("")
                continue
            item = data[i]
            if isinstance(item, dict):
                out.append(str(item.get("raw") or ""))
            else:
                out.append(str(item))
        return out

    if isinstance(data, dict):
        # {"1": "2", "q2": "1"} 或 {"answers":[...]}
        if isinstance(data.get("answers"), list):
            return _parse_answers_raw(json.dumps(data["answers"]), n)
        out = []
        for i in range(1, n + 1):
            key = str(i)
            if key in data:
                out.append(str(data[key]))
            else:
                out.append("")
        return out

    return [text] + [""] * (n - 1)


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
