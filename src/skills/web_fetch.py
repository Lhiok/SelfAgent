"""抓取公开网页正文（只读 GET）。"""

from __future__ import annotations

import re
from typing import Any

import httpx

from log import get_logger
from skills.base import Skill, SkillResult
from skills.http_util import truncate, validate_http_url

logger = get_logger("skills.web_fetch")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WS_RE.sub(" ", text).strip()


class WebFetchSkill(Skill):
    name = "web_fetch"
    description = (
        "用 HTTP GET 抓取公开网页，返回纯文本摘要（自动去 HTML 标签）。"
        "适合查阅文档；默认禁止访问内网地址。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["fetch"],
                "default": "fetch",
            },
            "url": {"type": "string", "description": "http(s) URL"},
            "max_chars": {
                "type": "integer",
                "default": 12000,
                "description": "返回正文最大字符数",
            },
            "timeout": {"type": "number", "default": 20},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        *,
        allow_private: bool = False,
        default_timeout: float = 20.0,
        max_chars: int = 12000,
        user_agent: str = "SelfAgent-web_fetch/0.1",
        enabled: bool = True,
    ) -> None:
        self.allow_private = allow_private
        self.default_timeout = default_timeout
        self.max_chars = max_chars
        self.user_agent = user_agent
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="web_fetch 已在配置中禁用")
        action = str(kwargs.get("action") or "fetch").strip().lower()
        if action != "fetch":
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        url = str(kwargs.get("url") or "").strip()
        err = validate_http_url(url, allow_private=self.allow_private)
        if err:
            return SkillResult(ok=False, output=err)

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        max_chars = int(kwargs.get("max_chars") or self.max_chars)
        logger.notice(f"web_fetch: {url}")
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            ) as client:
                resp = client.get(url)
        except httpx.HTTPError as exc:
            return SkillResult(ok=False, output=f"请求失败: {exc}")

        ctype = (resp.headers.get("content-type") or "").lower()
        body = resp.text or ""
        if "html" in ctype or body.lstrip().lower().startswith("<!doctype") or "<html" in body[:200].lower():
            text = _html_to_text(body)
        else:
            text = body.strip()
        text = truncate(text, max_chars)
        ok = 200 <= resp.status_code < 400
        return SkillResult(
            ok=ok,
            output=f"HTTP {resp.status_code} {url}\n\n{text}" if text else f"HTTP {resp.status_code} {url}（空正文）",
            data={
                "status_code": resp.status_code,
                "url": str(resp.url),
                "content_type": ctype,
                "chars": len(text),
            },
        )
