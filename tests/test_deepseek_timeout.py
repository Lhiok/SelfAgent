from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from ai.base import AIMessage
from ai.deepseek import DeepSeekClient, build_httpx_timeout
from permission import PermissionGuard
from session import Agent
from skills import SkillRegistry


def test_build_httpx_timeout_float_and_dict():
    t = build_httpx_timeout(180)
    assert t.read == 180.0
    assert t.connect == 10.0

    t2 = build_httpx_timeout({"connect": 5, "read": 240})
    assert t2.connect == 5.0
    assert t2.read == 240.0


def test_deepseek_retries_then_raises_friendly_error():
    client = DeepSeekClient(api_key="sk-test", timeout=1.0, retries=1)
    with patch("ai.deepseek.httpx.Client") as client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.side_effect = httpx.ReadTimeout("timed out")
        client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="超时|timeout"):
            client.chat([AIMessage(role="user", content="hi")])
        assert mock_client.post.call_count == 2


def test_agent_catches_ai_failure():
    class _Boom:
        provider = "boom"

        def chat(self, messages, options=None):
            raise RuntimeError("DeepSeek 请求超时或网络错误")

    agent = Agent(
        ai=_Boom(),  # type: ignore[arg-type]
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        detail="off",
        max_steps=3,
    )
    result = agent.run("hello")
    assert not result.completed
    assert "模型调用失败" in result.answer
    assert result.stop_reason == "error"
