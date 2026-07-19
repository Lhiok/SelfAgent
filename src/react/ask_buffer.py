"""ask_user 跨步暂存与一次性提交。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from log import get_logger
from skills.ask_user import coerce_questions
from skills.base import SkillResult

if TYPE_CHECKING:
    from react.agent import ReActAgent

logger = get_logger("react")


class AskBufferMixin:
    """混入 ReActAgent：ask_user 缓冲。"""

    def _parse_action_input(self: ReActAgent, action_input: str | None) -> dict[str, Any]:
        if not action_input or not str(action_input).strip():
            return {}
        try:
            data = json.loads(action_input)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _buffer_ask_user(self: ReActAgent, action_input: str | None) -> SkillResult:
        """暂存问题，不立即弹 UI；等 commit / Final Answer / 其它工具前再一次性询问。"""
        args = self._parse_action_input(action_input)
        if args.get("commit") or args.get("flush"):
            return self._flush_ask_buffer()

        items = coerce_questions(args)
        if not items:
            return SkillResult(
                ok=False,
                output="ask_user 需要 question+options，或非空 questions 数组",
            )

        for item in items:
            self._ask_seq += 1
            buffered = dict(item)
            buffered["id"] = str(self._ask_seq)
            self._ask_buffer.append(buffered)

        total = len(self._ask_buffer)
        added = len(items)
        logger.notice(f"ask_user 已暂存 {added} 题（合计 {total}）")
        self._emit_progress(
            {
                "type": "status",
                "phase": "ask_buffer",
                "message": f"已收集 {total} 个待确认问题…",
            }
        )
        return SkillResult(
            ok=True,
            output=(
                f"已暂存 {added} 个问题（合计 {total} 个，尚未询问用户）。"
                "请继续用 ask_user 补充其余待确认项；"
                "全部收集完后必须输出以 `Final Answer:` 开头的简短说明"
                "（可同时带 Plan: skill=... 步骤），"
                "系统会立即请用户确认上述全部问题。"
                "不要用不带 Final Answer: 前缀的纯文本结束。"
            ),
            data={"buffered": total, "added": added},
        )

    def _flush_ask_buffer(self: ReActAgent) -> SkillResult:
        if not self._ask_buffer:
            return SkillResult(ok=True, output="没有待确认的暂存问题", data={"questions": []})

        items = list(self._ask_buffer)
        self._ask_buffer.clear()
        logger.notice(f"ask_user 一次性提交 {len(items)} 题给用户")
        self._emit_progress(
            {
                "type": "status",
                "phase": "ask_user",
                "message": f"等待你确认 {len(items)} 个问题…",
            }
        )
        return self.skills.run(
            "ask_user",
            {"questions": items},
            permission=self.permission,
        )
