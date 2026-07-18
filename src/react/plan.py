"""Plan Mode：解析与执行计划。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from log import get_logger

logger = get_logger("react.plan")

PLAN_SYSTEM_PROMPT = """你当前处于 Plan Mode（规划模式）。
目标：先调研、再给出可执行计划，不要执行会修改系统的操作。

规则：
1. 如需了解现状，可以调用只读工具（例如 local_file 的 list/read）。
2. 若存在多种可行方案或关键细节不确定，必须先调用 ask_user 让用户选择，再据此完善计划。
3. 禁止执行写入类操作（write/patch/move 等）；需要修改时只能写入计划步骤。
4. 完成调研与确认后，必须输出如下格式（可含多个步骤）：

Thought: 计划思路
Plan:
1. skill=<工具名> | input=<JSON参数> | why=<这一步目的>
2. skill=<工具名> | input=<JSON参数> | why=<这一步目的>
Final Answer: 计划摘要（给用户看的说明）

ask_user 示例：
Action: ask_user
Action Input: {"question":"日志落盘选哪种？","options":["仅控制台","控制台+文件","控制台+文件+上报"],"default":"2"}

注意：Plan 中的步骤只是提案，系统不会在 Plan Mode 下自动执行写入步骤。
"""

_PLAN_BLOCK = re.compile(
    r"Plan:\s*(.*?)(?=\nFinal Answer:|\Z)",
    re.S | re.I,
)
_PLAN_STEP = re.compile(
    r"^\s*(\d+)\.\s*skill\s*=\s*(?P<skill>[^\s|]+)\s*\|\s*input\s*=\s*(?P<input>.+?)"
    r"(?:\s*\|\s*why\s*=\s*(?P<why>.*))?$",
    re.I | re.M,
)


@dataclass
class PlanStep:
    index: int
    skill: str
    arguments: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    raw_input: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "skill": self.skill,
            "arguments": self.arguments,
            "why": self.why,
            "raw_input": self.raw_input,
        }


@dataclass
class Plan:
    summary: str = ""
    thought: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    raw_text: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "thought": self.thought,
            "ok": self.ok,
            "steps": [s.to_dict() for s in self.steps],
        }

    def format_text(self) -> str:
        lines = ["Plan:"]
        for step in self.steps:
            payload = json.dumps(step.arguments, ensure_ascii=False)
            why = f" | why={step.why}" if step.why else ""
            lines.append(f"{step.index}. skill={step.skill} | input={payload}{why}")
        if self.summary:
            lines.append(f"Final Answer: {self.summary}")
        return "\n".join(lines)


def parse_plan(text: str, *, thought: str = "", final_answer: str = "") -> Plan:
    """从模型输出中解析 Plan 块。"""
    plan = Plan(thought=thought or "", summary=final_answer or "", raw_text=text or "")
    if not text:
        return plan

    block_match = _PLAN_BLOCK.search(text)
    block = block_match.group(1) if block_match else text

    for match in _PLAN_STEP.finditer(block):
        idx = int(match.group(1))
        skill = match.group("skill").strip()
        raw_input = match.group("input").strip()
        why = (match.group("why") or "").strip()
        args = _parse_json_object(raw_input)
        plan.steps.append(
            PlanStep(
                index=idx,
                skill=skill,
                arguments=args,
                why=why,
                raw_input=raw_input,
            )
        )

    plan.steps.sort(key=lambda s: s.index)
    if not plan.summary:
        # 兜底：无 Final Answer 时用 thought
        plan.summary = plan.thought
    return plan


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"value": data}
    except json.JSONDecodeError:
        # 尝试截取第一个 {...}
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        logger.warning(f"计划步骤参数不是合法 JSON: {raw[:120]}")
        return {"_raw": raw}
