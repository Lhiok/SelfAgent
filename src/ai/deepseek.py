"""DeepSeek 提供商（OpenAI 兼容 Chat Completions）。"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from ai.base import AIClient, AIMessage, AIResponse, ChatOptions
from env import get_env
from log import get_logger

logger = get_logger("ai.deepseek")


class DeepSeekClient(AIClient):
    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        api_key_env: str = "DEEPSEEK_API_KEY",
        base_url_env: str = "DEEPSEEK_BASE_URL",
        model_env: str = "DEEPSEEK_MODEL",
    ) -> None:
        self.api_key = api_key or get_env(api_key_env, default="")
        self.base_url = (
            base_url
            or get_env(base_url_env, default="https://api.deepseek.com", quiet=True)
        ).rstrip("/")
        self.model = (
            model
            or get_env(model_env, default="deepseek-chat", quiet=True)
            or "deepseek-chat"
        )
        self.timeout = timeout

        if not self.api_key:
            logger.critical("DeepSeek API Key 未配置")

    def chat(
        self,
        messages: Iterable[AIMessage],
        options: ChatOptions | None = None,
    ) -> AIResponse:
        options = options or ChatOptions()
        if not self.api_key:
            raise RuntimeError("DeepSeek API Key 未配置，无法发起请求")

        model = options.model or self.model
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content, **({"name": m.name} if m.name else {})}
                for m in messages
            ],
        }
        if options.temperature is not None:
            body["temperature"] = options.temperature
        if options.max_tokens is not None:
            body["max_tokens"] = options.max_tokens
        if options.extra:
            body.update(options.extra)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.notice(f"请求 DeepSeek: model={model}")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=body)
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"DeepSeek 请求失败: {exc}")
            raise

        if resp.status_code >= 400:
            logger.critical(f"DeepSeek HTTP {resp.status_code}: {data}")
            raise RuntimeError(f"DeepSeek 错误: {data}")

        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            logger.critical(f"DeepSeek 响应解析失败: {data}")
            raise RuntimeError("DeepSeek 响应格式异常") from exc

        return AIResponse(
            content=content,
            model=data.get("model", model),
            provider=self.provider,
            raw=data,
            usage=data.get("usage") or {},
        )
