"""测试用：按预设 tool_calls / 文本返回的 AIClient。"""

from __future__ import annotations

import json
from typing import Any

from ai.base import AIClient, AIMessage, AIResponse, ChatOptions, ToolCall


def tc(name: str, arguments: dict[str, Any] | str, *, id: str | None = None) -> ToolCall:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return ToolCall(id=id or f"call_{name}", name=name, arguments=args)


def finish(answer: str, *, id: str = "call_finish") -> ToolCall:
    return tc("finish", {"answer": answer}, id=id)


def submit_plan(
    summary: str,
    steps: list[dict[str, Any]],
    *,
    thought: str = "",
    id: str = "call_plan",
) -> ToolCall:
    return tc(
        "submit_plan",
        {"summary": summary, "thought": thought, "steps": steps},
        id=id,
    )


def enter_plan(reason: str = "need plan", *, id: str = "call_enter_plan") -> ToolCall:
    return tc("enter_plan_mode", {"reason": reason}, id=id)


class ScriptedAI(AIClient):
    provider = "scripted"

    def __init__(self, turns: list[AIResponse | dict[str, Any] | str]) -> None:
        self.turns = list(turns)
        self.n = 0
        self.calls: list[list[AIMessage]] = []

    def chat(
        self,
        messages,
        options: ChatOptions | None = None,
    ) -> AIResponse:
        self.calls.append(list(messages))
        if self.n >= len(self.turns):
            return AIResponse(
                content="",
                model="s",
                provider=self.provider,
                tool_calls=[finish("done")],
            )
        item = self.turns[self.n]
        self.n += 1
        if isinstance(item, AIResponse):
            return item
        if isinstance(item, str):
            return AIResponse(content=item, model="s", provider=self.provider)
        # dict: content / tool_calls
        tool_calls = item.get("tool_calls") or []
        parsed: list[ToolCall] = []
        for t in tool_calls:
            if isinstance(t, ToolCall):
                parsed.append(t)
            elif isinstance(t, dict):
                parsed.append(
                    ToolCall(
                        id=str(t.get("id") or "c"),
                        name=str(t.get("name") or ""),
                        arguments=(
                            t.get("arguments")
                            if isinstance(t.get("arguments"), str)
                            else json.dumps(t.get("arguments") or {}, ensure_ascii=False)
                        ),
                    )
                )
        return AIResponse(
            content=str(item.get("content") or ""),
            model="s",
            provider=self.provider,
            tool_calls=parsed,
        )


def resp(*tool_calls: ToolCall, content: str = "") -> AIResponse:
    return AIResponse(
        content=content,
        model="s",
        provider="scripted",
        tool_calls=list(tool_calls),
    )
