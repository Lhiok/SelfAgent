"""AI 层抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class AIMessage:
    role: Role
    content: str
    name: str | None = None


@dataclass
class ChatOptions:
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIResponse:
    content: str
    model: str
    provider: str
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    """流式增量事件；text 为本次 delta。"""

    text: str
    done: bool = False


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
