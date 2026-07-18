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
2. 若存在多种可行方案或关键细节不确定，必须先调用 ask_user 收集问题，再据此完善计划。
3. 多个待确认点：优先一次 ask_user 用 questions 数组提出；也可连续多次 ask_user（系统会暂存，等你输出 Plan 时一并请用户确认，无需等待用户逐题作答）。
4. 禁止执行写入类操作（write/patch/move 等）；需要修改时只能写入计划步骤。
5. 完成调研与确认后，若要产出可执行计划，必须输出如下可解析格式（可含多个步骤；skill= 行不可省略）：

Thought: 计划思路
Plan:
1. skill=<工具名> | input=<JSON参数> | why=<这一步目的>
2. skill=<工具名> | input=<JSON参数> | why=<这一步目的>
Final Answer: 计划摘要（给用户看的简短说明；不要重复粘贴 Plan 步骤或大段 JSON）

若只需陈述推荐方案、尚无 skill= 步骤，也可直接 Final Answer 说明，由用户在下一轮对话继续。

ask_user 单问示例：
Action: ask_user
Action Input: {"question":"日志落盘选哪种？","options":["A. 仅控制台——最简单","B. 控制台+文件——推荐排查","C. 控制台+文件+上报"],"default":"B"}

ask_user 多问示例（推荐）：
Action: ask_user
Action Input: {"questions":[{"question":"缓存策略？","options":["A. 内存缓存——低延迟","B. Redis——可共享"]},{"question":"日志级别？","options":["A. info","B. debug"]}]}

注意：options 必须是完整可读文案，禁止只写 ["A","B","C"]；方案说明放在 options，不要只写在 question 正文里。

注意：Plan 中的步骤只是提案，系统不会在 Plan Mode 下自动执行写入步骤。
用户点「确认执行」后才会真正执行。
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
        # 同行粘连下一步时，截断 why
        why = re.split(r"\s+\d+\.\s*skill\s*=", why, maxsplit=1)[0].strip()
        why = why.strip("\"'")
        # input 也可能吞进后续步骤
        if re.search(r"\d+\.\s*skill\s*=", raw_input):
            raw_input = re.split(r"\s+\d+\.\s*skill\s*=", raw_input, maxsplit=1)[0].strip()
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

    loose_steps = _parse_plan_steps_loose(block)
    if len(loose_steps) > len(plan.steps):
        plan.steps = loose_steps
    elif not plan.steps:
        plan.steps = loose_steps

    plan.steps.sort(key=lambda s: s.index)
    if not plan.summary:
        # 兜底：无 Final Answer 时用 thought
        plan.summary = plan.thought
    plan.summary = clean_plan_summary(plan.summary, has_steps=bool(plan.steps))
    return plan


def looks_like_choice_prompt(text: str) -> bool:
    """识别把问卷/选项表误写进 Final Answer 的内容。"""
    s = (text or "").strip()
    if not s:
        return False
    if re.search(r"请回复你的选择|请回复.*选择|请选择下列|请从下列方案", s):
        return True
    # Markdown 表格式问卷（多行 |...| 且含方案 A/B）
    pipe_rows = len(re.findall(r"^\s*\|.+\|\s*$", s, flags=re.M))
    if pipe_rows >= 4 and re.search(r"方案\s*[ABC]|^\s*\|\s*A\s*[\|：:]", s, flags=re.M):
        return True
    if pipe_rows >= 4 and re.search(r"问题\s*\d+", s) and re.search(r"\|\s*A\s*\|", s):
        return True
    return False


def clean_plan_summary(summary: str, *, has_steps: bool = False) -> str:
    """去掉摘要里误粘贴的 Plan / skill= JSON / 问卷表，避免 UI 变成墙式原文。"""
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
        head = text[: m_step.start()].rstrip(" \t\r\n：:")
        text = head

    if has_steps and re.search(r"(?i)skill\s*=\s*\w+\s*\|\s*input\s*=", text):
        # 摘要几乎全是计划原文
        return "计划已就绪，请确认后执行。"

    if has_steps and looks_like_choice_prompt(text):
        return "计划已就绪，请确认后执行。"

    return text.strip() or ("计划已就绪，请确认后执行。" if has_steps else "")


def _parse_plan_steps_loose(block: str) -> list[PlanStep]:
    """容错解析：支持步骤挤在同一行、input 为跨行 JSON。"""
    text = block or ""
    marker = re.compile(r"(\d+)\.\s*skill\s*=\s*([^\s|]+)\s*\|\s*input\s*=\s*", re.I)
    matches = list(marker.finditer(text))
    steps: list[PlanStep] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        raw_input = chunk
        why = ""
        if chunk.startswith("{"):
            json_end = _json_object_end(chunk)
            if json_end >= 0:
                raw_input = chunk[: json_end + 1]
                rest = chunk[json_end + 1 :]
                why_m = re.search(r"\|\s*why\s*=\s*(.*)$", rest, re.I | re.S)
                if why_m:
                    why = why_m.group(1).strip().strip("\"'")
        else:
            why_m = re.search(r"\|\s*why\s*=\s*(.*)$", chunk, re.I | re.S)
            if why_m:
                raw_input = chunk[: why_m.start()].strip()
                why = why_m.group(1).strip().strip("\"'")
        why = re.split(r"\s+\d+\.\s*skill\s*=", why, maxsplit=1)[0].strip().strip("\"'")
        steps.append(
            PlanStep(
                index=int(match.group(1)),
                skill=match.group(2).strip(),
                arguments=_parse_json_object(raw_input),
                why=why,
                raw_input=raw_input,
            )
        )
    return steps


def _json_object_end(text: str) -> int:
    if not text or text[0] != "{":
        return -1
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


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
