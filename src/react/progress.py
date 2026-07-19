"""进度与细节输出。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from log import get_logger
from react.changes import extract_change_from_call, merge_changes_by_path
from react.detail import DETAIL_OFF, format_result_detail, format_step_detail
from react.types import ReActResult, ReActStep

if TYPE_CHECKING:
    from react.agent import ReActAgent

logger = get_logger("react")


class ProgressMixin:
    """混入 ReActAgent：进度回调与细节打印。"""

    def _emit_progress(self: ReActAgent, event: dict[str, Any]) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"on_progress 回调失败: {exc}")

    def _step_progress_payload(self: ReActAgent, step: ReActStep) -> dict[str, Any]:
        level = self.detail if self.detail != DETAIL_OFF else "summary"
        max_chars = self.detail_max_chars
        actions: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        for call in step.calls:
            item: dict[str, Any] = {
                "action": call.action,
                "ok": call.ok,
            }
            try:
                payload = json.loads(call.action_input or "")
                if isinstance(payload, dict) and payload.get("action"):
                    item["op"] = str(payload.get("action"))
                    if payload.get("path"):
                        item["path"] = str(payload.get("path")).replace("\\", "/")
            except (TypeError, json.JSONDecodeError):
                pass
            inp = (call.action_input or "").strip()
            if inp:
                item["input"] = inp if len(inp) <= 240 else inp[:239] + "…"
            obs = (call.observation or "").strip()
            if obs:
                item["observation"] = (
                    obs if len(obs) <= max_chars else obs[: max_chars - 1] + "…"
                )
            change = extract_change_from_call(
                skill_name=call.action,
                action_input=call.action_input,
                data=call.data,
                ok=call.ok,
            )
            if change:
                item["change"] = change
                changes.append(change)
            actions.append(item)
        todos = None
        for call in step.calls:
            if (
                call.action == "todo_tracker"
                and call.ok
                and isinstance(call.data, dict)
                and isinstance(call.data.get("items"), list)
            ):
                todos = {
                    "items": call.data.get("items") or [],
                    "counts": call.data.get("counts") or {},
                }
        payload: dict[str, Any] = {
            "type": "step",
            "index": step.index,
            "thought": (step.thought or "").strip(),
            "actions": actions,
            "changes": merge_changes_by_path(changes, max_chars=max_chars),
            "final_answer": step.final_answer,
            "text": format_step_detail(step, level, max_chars=max_chars),
        }
        if todos is not None:
            payload["todos"] = todos
        return payload

    def _emit_step(self: ReActAgent, step: ReActStep) -> None:
        self._emit_progress(self._step_progress_payload(step))

        if self.detail == DETAIL_OFF:
            return
        text = format_step_detail(
            step, self.detail, max_chars=self.detail_max_chars
        )
        if not text:
            return
        logger.notice(f"ReAct 细节\n{text}")
        if not self.stream_detail:
            return
        if self.on_detail is not None:
            self.on_detail(text)
        else:
            print(text, flush=True)

    def _finalize_result(self: ReActAgent, result: ReActResult) -> ReActResult:
        result.detail_level = self.detail
        if self.detail != DETAIL_OFF:
            result.detail_text = format_result_detail(
                result, self.detail, max_chars=self.detail_max_chars
            )
        return result
