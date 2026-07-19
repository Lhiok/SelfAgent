"""AI 层抽象接口（支持 OpenAI 兼容 tools / tool_calls）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class AIMessage:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_api_dict(self) -> dict[str, Any]:
        """转为 OpenAI 兼容 messages 项。"""
        item: dict[str, Any] = {"role": self.role, "content": self.content or ""}
        if self.name:
            item["name"] = self.name
        if self.role == "tool" and self.tool_call_id:
            item["tool_call_id"] = self.tool_call_id
        if self.role == "assistant" and self.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments or "{}"},
                }
                for tc in self.tool_calls
            ]
            # 部分提供商要求有 tool_calls 时 content 可为 null
            if not self.content:
                item["content"] = None
        return item


@dataclass
class ChatOptions:
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIResponse:
    content: str
    model: str
    provider: str
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class StreamEvent:
    """流式增量事件；text 为本次 delta。"""

    text: str
    done: bool = False


def parse_tool_calls_from_message(message: dict[str, Any]) -> list[ToolCall]:
    raw = message.get("tool_calls") or []
    out: list[ToolCall] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        fn = item.get("function") or {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        args = fn.get("arguments")
        if args is None:
            args = "{}"
        elif not isinstance(args, str):
            import json

            args = json.dumps(args, ensure_ascii=False)
        tc_id = str(item.get("id") or f"call_{i}")
        out.append(ToolCall(id=tc_id, name=name, arguments=args or "{}"))
    return out


class AIClient(ABC):
    provider: str

    @abstractmethod
    def chat(
        self,
        messages: Iterable[AIMessage],
        options: ChatOptions | None = None,
    ) -> AIResponse:
        raise NotImplementedError

    def chat_stream(
        self,
        messages: Iterable[AIMessage],
        options: ChatOptions | None = None,
    ) -> Iterator[str | StreamEvent]:
        """默认实现：一次性 chat 后 yield 全文（子类可覆盖为真流式）。"""
        response = self.chat(messages, options)
        content = response.content or ""
        if content:
            yield StreamEvent(text=content, done=True)

    def ask(self, prompt: str, *, system: str | None = None, **kwargs: Any) -> str:
        messages: list[AIMessage] = []
        if system:
            messages.append(AIMessage(role="system", content=system))
        messages.append(AIMessage(role="user", content=prompt))
        return self.chat(messages, ChatOptions(**kwargs) if kwargs else None).content
