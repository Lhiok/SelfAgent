"""DeepSeek 提供商（OpenAI 兼容 Chat Completions）。"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from ai.base import AIClient, AIMessage, AIResponse, ChatOptions
from env import get_env
from log import get_logger

logger = get_logger("ai.deepseek")


def build_httpx_timeout(timeout: float | int | dict[str, Any] | httpx.Timeout) -> httpx.Timeout:
    """将配置中的 timeout 转为 httpx.Timeout（区分 connect / read）。"""
    if isinstance(timeout, httpx.Timeout):
        return timeout
    if isinstance(timeout, dict):
        read = float(timeout.get("read", timeout.get("timeout", 180.0)))
        connect = float(timeout.get("connect", 10.0))
        write = float(timeout.get("write", min(30.0, read)))
        pool = float(timeout.get("pool", 10.0))
        return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)
    read = float(timeout)
    return httpx.Timeout(
        connect=min(10.0, read),
        read=read,
        write=min(30.0, read),
        pool=10.0,
    )


class DeepSeekClient(AIClient):
    provider = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | dict[str, Any] = 180.0,
        retries: int = 1,
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
        self.timeout = build_httpx_timeout(timeout)
        self.retries = max(0, int(retries))

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

        logger.notice(
            f"请求 DeepSeek: model={model} read_timeout={self.timeout.read}s "
            f"retries={self.retries}"
        )
        data: dict[str, Any] | None = None
        resp: httpx.Response | None = None
        last_exc: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=headers, json=body)
                    data = resp.json()
                last_exc = None
                break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    logger.warning(
                        f"DeepSeek 网络/超时，重试 {attempt + 1}/{self.retries}: {exc}"
                    )
                    continue
                logger.critical(f"DeepSeek 请求失败: {exc}")
                raise RuntimeError(
                    f"DeepSeek 请求超时或网络错误（read={self.timeout.read}s，"
                    f"已重试 {self.retries} 次）。可增大 config.ai.deepseek.timeout 后重试。"
                    f" 原因: {exc}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                logger.critical(f"DeepSeek 请求失败: {exc}")
                raise

        if last_exc is not None or resp is None or data is None:
            raise RuntimeError(f"DeepSeek 请求失败: {last_exc}")

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
