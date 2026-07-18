"""解析 ReAct 格式输出（支持一次输出多个 Action）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ParsedAction:
    action: str
    action_input: str = ""


@dataclass
class ParsedReAct:
    thought: str = ""
    actions: list[ParsedAction] = field(default_factory=list)
    final_answer: str | None = None

    @property
    def action(self) -> str | None:
        """兼容单 Action 用法：返回第一个工具名。"""
        return self.actions[0].action if self.actions else None

    @property
    def action_input(self) -> str | None:
        """兼容单 Action 用法：返回第一个工具参数。"""
        return self.actions[0].action_input if self.actions else None


_THOUGHT = re.compile(
    r"Thought:\s*(.*?)(?=\n(?:Action|Final Answer):|\Z)",
    re.S | re.I,
)
_FINAL = re.compile(r"Final Answer:\s*(.*)\Z", re.S | re.I)
# 连续多组 Action / Action Input；Action Input 可选
_ACTION_PAIR = re.compile(
    r"Action:\s*(?P<action>[^\n]+)"
    r"(?:\nAction Input:\s*(?P<input>.*?)(?=\n(?:Action|Thought|Final Answer):|\Z))?",
    re.S | re.I,
)


_LEAKED_PROTOCOL = re.compile(
    r"(?:^|\n)\s*(?:\*{0,2})Action(?:\s*Input)?\s*[:：].*\Z",
    re.S | re.I,
)


def _clean_thought(thought: str) -> str:
    """去掉误粘进 Thought 的 Action / Action Input 协议段。"""
    text = (thought or "").strip()
    if not text:
        return ""
    text = _LEAKED_PROTOCOL.sub("", text).strip()
    return text


def parse_react_output(text: str) -> ParsedReAct:
    result = ParsedReAct()
    if not text:
        return result

    m_thought = _THOUGHT.search(text)
    if m_thought:
        result.thought = _clean_thought(m_thought.group(1))

    m_final = _FINAL.search(text)
    if m_final:
        result.final_answer = m_final.group(1).strip()
        return result

    for match in _ACTION_PAIR.finditer(text):
        name = (match.group("action") or "").strip()
        if not name:
            continue
        raw_input = match.group("input")
        result.actions.append(
            ParsedAction(
                action=name,
                action_input=(raw_input or "").strip(),
            )
        )

    return result
