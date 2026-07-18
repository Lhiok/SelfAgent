"""按配置 / 参数创建 AI 客户端。"""

from __future__ import annotations

from typing import Any

import config as cfg
from ai.base import AIClient
from ai.deepseek import DeepSeekClient
from log import get_logger

logger = get_logger("ai")

_PROVIDERS = {
    "deepseek": DeepSeekClient,
}


def create_ai_client(
    provider: str | None = None,
    **overrides: Any,
) -> AIClient:
    """
    创建 AI 客户端。
    provider 优先取参数，其次取配置 ai.default_provider，默认 deepseek。
    """
    ai_cfg = cfg.get_section("ai", {}) or {}
    name = (provider or ai_cfg.get("default_provider") or "deepseek").strip().lower()

    if name not in _PROVIDERS:
        logger.critical(f"不支持的 AI 提供商: {name}")
        raise ValueError(f"不支持的 AI 提供商: {name}，当前可用: {', '.join(_PROVIDERS)}")

    provider_cfg = dict(ai_cfg.get(name) or {})
    # 配置中的空字符串不覆盖
    cleaned = {k: v for k, v in provider_cfg.items() if v not in (None, "")}
    cleaned.update({k: v for k, v in overrides.items() if v is not None})

    # 映射配置字段到构造参数
    ctor_kwargs: dict[str, Any] = {}
    mapping = {
        "api_key": "api_key",
        "base_url": "base_url",
        "model": "model",
        "timeout": "timeout",
        "api_key_env": "api_key_env",
        "base_url_env": "base_url_env",
        "model_env": "model_env",
    }
    for src, dest in mapping.items():
        if src in cleaned:
            ctor_kwargs[dest] = cleaned[src]

    logger.notice(f"创建 AI 客户端: provider={name}")
    return _PROVIDERS[name](**ctor_kwargs)


def register_provider(name: str, cls: type[AIClient]) -> None:
    """扩展注册新的 AI 提供商。"""
    _PROVIDERS[name.strip().lower()] = cls
