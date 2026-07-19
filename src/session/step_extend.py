"""步数用尽时询问用户是否延长。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai import AIMessage
from log import get_logger
from session.types import ActionCall, AgentStep

if TYPE_CHECKING:
    from agent.facade import Agent

logger = get_logger("session")


class StepExtendMixin:
    """混入 Agent：步数延长。"""

    def _prompt_extend_steps(
        self: Agent,
        *,
        steps: list[AgentStep],
        messages: list[AIMessage],
        used: int,
        budget: int,
        hard_cap: int,
    ) -> int | None:
        """步数用尽时询问用户是否增加本轮可用步数。返回增量，拒绝则为 None。"""
        remain = max(0, hard_cap - budget)
        options: list[str] = []
        extend_map: dict[int, int] = {}
        for n in self.step_extend_options:
            if n <= 0 or n > remain:
                continue
            idx = len(options) + 1
            label = f"{chr(64 + idx)}. 再增加 {n} 步继续（上限将变为 {budget + n}）"
            options.append(label)
            extend_map[idx] = n
        options.append(f"{chr(64 + len(options) + 1)}. 结束本轮，不再增加步数")
        stop_idx = len(options)

        if not extend_map:
            return None

        question = (
            f"本轮对话已用尽可用步数（{used}/{budget}）。"
            f"硬上限为 {hard_cap}。"
            "是否增加此轮可用步数以继续任务？"
        )
        payload = {
            "id": "extend_steps",
            "question": question,
            "options": options,
            "allow_multiple": False,
            "allow_custom": False,
            "default": "1",
            "context": "选择加步后将从断点继续；选结束则本轮停止。",
        }
        self._emit_progress(
            {
                "type": "status",
                "phase": "ask_user",
                "message": "等待你确认是否增加本轮步数…",
            }
        )
        result = self.skills.run(
            "ask_user",
            {"questions": [payload]},
            permission=self.permission,
            enforce_permission=False,
        )
        ask_step = AgentStep(
            index=len(steps) + 1,
            thought="步数用尽，询问用户是否延长本轮可用步数",
            calls=[
                ActionCall(
                    action="ask_user",
                    action_input='{"questions":[{"id":"extend_steps"}]}',
                    observation=result.output if result.ok else f"ERROR: {result.output}",
                    ok=result.ok,
                    data=dict(result.data or {}),
                )
            ],
        )
        steps.append(ask_step)
        self._emit_step(ask_step)
        messages.append(
            AIMessage(
                role="assistant",
                content=(
                    "Thought: 步数已用尽，询问用户是否增加本轮可用步数\n"
                    "Action: ask_user\n"
                    'Action Input: {"id":"extend_steps"}\n'
                ),
            )
        )
        messages.append(
            AIMessage(
                role="user",
                content=f"Observation:\n{result.output if result.ok else 'ERROR: ' + result.output}",
            )
        )
        if not result.ok:
            logger.warning(f"询问加步失败: {result.output}")
            return None
        return self._parse_extend_choice(result.data, extend_map, stop_idx)

    def _parse_extend_choice(
        self: Agent,
        data: dict[str, Any] | None,
        extend_map: dict[int, int],
        stop_idx: int,
    ) -> int | None:
        if not isinstance(data, dict):
            return None
        row = data
        if isinstance(data.get("questions"), list) and data["questions"]:
            first = data["questions"][0]
            if isinstance(first, dict):
                row = first
        indexes = row.get("indexes")
        if isinstance(indexes, list) and indexes:
            try:
                choice = int(indexes[0])
            except (TypeError, ValueError):
                choice = None
            else:
                if choice == stop_idx:
                    return None
                if choice in extend_map:
                    return extend_map[choice]
        selected = row.get("selected")
        texts = selected if isinstance(selected, list) else ([selected] if selected else [])
        for text in texts:
            s = str(text)
            if "结束" in s or "不再增加" in s:
                return None
            for idx, n in extend_map.items():
                if f"增加 {n} 步" in s or f"+{n}" in s:
                    return n
                if s.strip().upper().startswith(chr(64 + idx)):
                    return n
        raw = str(row.get("raw") or "").strip()
        if raw.isdigit():
            choice = int(raw)
            if choice == stop_idx:
                return None
            if choice in extend_map:
                return extend_map[choice]
        return None
