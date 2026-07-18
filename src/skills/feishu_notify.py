"""通过飞书机器人推送消息。"""

from __future__ import annotations

import json
from typing import Any

from feishu import FeishuBot
from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.feishu_notify")


class FeishuNotifySkill(Skill):
    name = "feishu_notify"
    description = (
        "向飞书自定义机器人推送通知：text 纯文本，markdown 卡片。"
        "适合汇报计划摘要、任务完成结果。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["text", "markdown"],
                "description": "消息类型",
            },
            "text": {"type": "string", "description": "text 模式下的正文"},
            "title": {
                "type": "string",
                "description": "markdown 模式下的标题",
                "default": "SelfAgent 通知",
            },
            "content": {
                "type": "string",
                "description": "markdown 模式下的正文（支持部分 Markdown）",
            },
            "webhook_url": {
                "type": "string",
                "description": "可选，覆盖默认 Webhook",
            },
        },
        "required": ["action"],
    }

    def __init__(self, bot: FeishuBot | None = None, *, enabled: bool = True) -> None:
        self.bot = bot
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="feishu_notify 已在配置中禁用")

        action = str(kwargs.get("action") or "").strip().lower()
        webhook = str(kwargs.get("webhook_url") or "").strip()
        bot = self.bot if self.bot is not None and not webhook else FeishuBot(
            webhook_url=webhook or None
        )

        if action == "text":
            text = str(kwargs.get("text") or "").strip()
            if not text:
                return SkillResult(ok=False, output="text 模式需要 text 字段")
            resp = bot.send_text(text)
        elif action == "markdown":
            title = str(kwargs.get("title") or "SelfAgent 通知").strip()
            content = str(kwargs.get("content") or kwargs.get("text") or "").strip()
            if not content:
                return SkillResult(ok=False, output="markdown 模式需要 content（或 text）")
            resp = bot.send_markdown(title, content)
        else:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        payload = {
            "action": action,
            "ok": resp.ok,
            "code": resp.code,
            "msg": resp.msg,
        }
        logger.notice(f"feishu_notify action={action} ok={resp.ok}")
        if not resp.ok:
            return SkillResult(
                ok=False,
                output=json.dumps(payload, ensure_ascii=False),
                data=payload,
            )
        return SkillResult(
            ok=True,
            output=json.dumps(payload, ensure_ascii=False),
            data=payload,
        )
