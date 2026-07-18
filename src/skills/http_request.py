"""通用 HTTP 请求（受 SSRF 限制）。"""

from __future__ import annotations

import json
from typing import Any

import httpx

from log import get_logger
from skills.base import Skill, SkillResult
from skills.http_util import truncate, validate_http_url

logger = get_logger("skills.http_request")


class HttpRequestSkill(Skill):
    name = "http_request"
    description = (
        "发送 HTTP 请求（GET/POST/PUT/PATCH/DELETE），返回状态码与响应正文摘要。"
        "默认禁止内网；可用于调用公开 API 做验收。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["request", "get", "post", "put", "patch", "delete"],
                "default": "request",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                "default": "GET",
            },
            "url": {"type": "string"},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "json_body": {
                "description": "JSON 请求体（对象/数组）",
            },
            "body": {
                "type": "string",
                "description": "原始文本请求体（与 json_body 二选一）",
            },
            "timeout": {"type": "number", "default": 30},
            "max_chars": {"type": "integer", "default": 20000},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        *,
        allow_private: bool = False,
        default_timeout: float = 30.0,
        max_chars: int = 20000,
        user_agent: str = "SelfAgent-http_request/0.1",
        enabled: bool = True,
    ) -> None:
        self.allow_private = allow_private
        self.default_timeout = default_timeout
        self.max_chars = max_chars
        self.user_agent = user_agent
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="http_request 已在配置中禁用")

        action = str(kwargs.get("action") or "request").strip().lower()
        method_map = {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
        }
        if action in method_map:
            method = method_map[action]
        elif action == "request":
            method = str(kwargs.get("method") or "GET").strip().upper()
        else:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            return SkillResult(ok=False, output=f"不支持的 method: {method}")

        url = str(kwargs.get("url") or "").strip()
        err = validate_http_url(url, allow_private=self.allow_private)
        if err:
            return SkillResult(ok=False, output=err)

        headers = {"User-Agent": self.user_agent}
        raw_headers = kwargs.get("headers")
        if isinstance(raw_headers, dict):
            for k, v in raw_headers.items():
                headers[str(k)] = str(v)

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        max_chars = int(kwargs.get("max_chars") or self.max_chars)
        json_body = kwargs.get("json_body")
        body = kwargs.get("body")

        logger.notice(f"http_request: {method} {url}")
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                if json_body is not None:
                    resp = client.request(method, url, headers=headers, json=json_body)
                elif body is not None:
                    resp = client.request(method, url, headers=headers, content=str(body))
                else:
                    resp = client.request(method, url, headers=headers)
        except httpx.HTTPError as exc:
            return SkillResult(ok=False, output=f"请求失败: {exc}")

        text = resp.text or ""
        try:
            parsed = resp.json()
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        text = truncate(text, max_chars)
        ok = 200 <= resp.status_code < 400
        return SkillResult(
            ok=ok,
            output=f"{method} {url} → HTTP {resp.status_code}\n\n{text}",
            data={
                "status_code": resp.status_code,
                "url": str(resp.url),
                "method": method,
                "content_type": resp.headers.get("content-type", ""),
            },
        )
