"""Plan 数据模型与摘要清理。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from log import get_logger

logger = get_logger("plan.model")


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


def looks_like_choice_prompt(text: str) -> bool:
    """识别把问卷/选项表误写进摘要的内容。"""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"请回复你的选择|请回复.*选择|请选择下列|请从下列方案", s):
        return True
    pipe_rows = len(re.findall(r"^\s*\|.+\|\s*$", s, flags=re.M))
    if pipe_rows >= 4 and re.search(r"方案\s*[ABC]|^\s*\|\s*A\s*[\|：:]", s, flags=re.M):
        return True
    if pipe_rows >= 4 and re.search(r"问题\s*\d+", s) and re.search(r"\|\s*A\s*\|", s):
        return True
    return False


def clean_plan_summary(summary: str, *, has_steps: bool = False) -> str:
    """去掉摘要里误粘贴的 Plan / skill= JSON / 问卷表。"""
    text = (summary or "").strip()
    if not text:
        return text

    if looks_like_choice_prompt(text):
        return "计划已就绪，请确认后执行。" if has_steps else text

    m_plan = re.search(r"(?i)\bPlan\s*:", text)
    if m_plan and "skill=" in text[m_plan.start() :]:
        text = text[: m_plan.start()].rstrip(" \t\r\n：:")

    m_step = re.search(r"(?i)\d+\.\s*skill\s*=", text)
    if m_step and "input=" in text[m_step.start() :]:
        text = text[: m_step.start()].rstrip(" \t\r\n：:")

    if has_steps and re.search(r"(?i)skill\s*=\s*\w+\s*\|\s*input\s*=", text):
        return "计划已就绪，请确认后执行。"

    if has_steps and looks_like_choice_prompt(text):
        return "计划已就绪，请确认后执行。"

    return text.strip() or ("计划已就绪，请确认后执行。" if has_steps else "")
