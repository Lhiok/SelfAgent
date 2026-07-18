"""飞书层：机器人推送（自定义机器人 Webhook）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

import config as cfg
from env import get_env
from log import get_logger

logger = get_logger("feishu")


@dataclass
class FeishuResponse:
    ok: bool
    code: int | None
    msg: str
    raw: dict[str, Any]


class FeishuBot:
    """飞书自定义机器人。"""

    def __init__(self, webhook_url: str | None = None, *, timeout: float = 10.0) -> None:
        self.timeout = timeout
        # None=从配置/环境解析；显式传 "" 表示禁用
        self.webhook_url = self._resolve_webhook() if webhook_url is None else webhook_url

    def _resolve_webhook(self) -> str:
        section = cfg.get_section("feishu", {}) or {}
        direct = section.get("webhook_url") or ""
        if direct:
            return str(direct)
        env_name = section.get("webhook_url_env") or "FEISHU_WEBHOOK_URL"
        return get_env(str(env_name), default="", quiet=True)

    def send_text(self, text: str) -> FeishuResponse:
        return self._post({"msg_type": "text", "content": {"text": text}})

    def send_markdown(self, title: str, content: str) -> FeishuResponse:
        """interactive 卡片，正文支持部分 Markdown。"""
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}}
                ],
            },
        }
        return self._post(payload)

    def send_post(
        self,
        title: str,
        lines: list[str] | None = None,
        *,
        zh_cn: list[list[dict[str, Any]]] | None = None,
    ) -> FeishuResponse:
        """富文本 post 消息。"""
        content = zh_cn
        if content is None:
            content = [[{"tag": "text", "text": line}] for line in (lines or [])]
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": content,
                    }
                }
            },
        }
        return self._post(payload)

    def send_raw(self, payload: dict[str, Any]) -> FeishuResponse:
        return self._post(payload)

    def _post(self, payload: dict[str, Any]) -> FeishuResponse:
        if not self.webhook_url:
            logger.critical("飞书 Webhook 未配置，无法推送")
            return FeishuResponse(ok=False, code=None, msg="webhook missing", raw={})

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.webhook_url, json=payload)
                data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001 — 统一封装为响应
            logger.critical(f"飞书推送失败: {exc}")
            return FeishuResponse(ok=False, code=None, msg=str(exc), raw={})

        code = data.get("code", data.get("StatusCode"))
        msg = str(data.get("msg") or data.get("StatusMessage") or resp.reason_phrase)
        ok = resp.is_success and (code in (0, None, "0"))
        if not ok:
            logger.warning(f"飞书推送返回异常: code={code}, msg={msg}")
        else:
            logger.notice("飞书推送成功")
        return FeishuResponse(ok=ok, code=code if isinstance(code, int) else None, msg=msg, raw=data)
