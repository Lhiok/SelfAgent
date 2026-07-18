"""现有 Skill 不足时，向用户提交能力需求（落盘 + 飞书通知，不阻塞当前任务）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from feishu import FeishuBot
from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.request_capability")

_PRIORITY = {"low", "medium", "high"}
_SLUG_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


class RequestCapabilitySkill(Skill):
    name = "request_capability"
    description = (
        "当认定现有 Skill 无法满足某项工作时，向用户提交能力/功能需求。"
        "需求不会立即实现：仅在本地指定目录写入需求文档，并可选飞书通知用户。"
        "提交后必须继续用现有 Skill 尽力完成当前任务，不要等待需求落地。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "需求标题（简短）",
            },
            "need": {
                "type": "string",
                "description": "希望新增的能力或功能描述",
            },
            "why": {
                "type": "string",
                "description": "为何现有 Skill 无法满足（已尝试或评估过的能力）",
            },
            "context": {
                "type": "string",
                "description": "任务背景、相关路径或约束",
            },
            "workaround": {
                "type": "string",
                "description": "当前将如何用现有 Skill 继续推进",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "description": "优先级",
            },
            "notify": {
                "type": "boolean",
                "default": True,
                "description": "是否飞书通知用户（仍受配置 notify_feishu 约束）",
            },
        },
        "required": ["title", "need", "why"],
    }

    def __init__(
        self,
        output_dir: str | Path = "requirements",
        *,
        notify_feishu: bool = True,
        bot: FeishuBot | None = None,
        enabled: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.notify_feishu = notify_feishu
        self.bot = bot
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="request_capability 已在配置中禁用")

        title = str(kwargs.get("title") or "").strip()
        need = str(kwargs.get("need") or "").strip()
        why = str(kwargs.get("why") or "").strip()
        if not title:
            return SkillResult(ok=False, output="request_capability 需要 title")
        if not need:
            return SkillResult(ok=False, output="request_capability 需要 need")
        if not why:
            return SkillResult(ok=False, output="request_capability 需要 why")

        context = str(kwargs.get("context") or "").strip()
        workaround = str(kwargs.get("workaround") or "").strip()
        priority = str(kwargs.get("priority") or "medium").strip().lower()
        if priority not in _PRIORITY:
            return SkillResult(
                ok=False,
                output=f"priority 须为 low/medium/high，收到: {priority}",
            )

        notify_flag = kwargs.get("notify")
        # 配置 notify_feishu 为总开关；调用方可再传 notify=false 关闭本次通知
        call_notify = True if notify_flag is None else bool(notify_flag)
        want_notify = self.notify_feishu and call_notify

        created_at = datetime.now().astimezone()
        stamp = created_at.strftime("%Y%m%d_%H%M%S")
        slug = _slugify(title)
        filename = f"{stamp}_{slug}.md"
        path = self.output_dir / filename

        body = _render_markdown(
            title=title,
            need=need,
            why=why,
            context=context,
            workaround=workaround,
            priority=priority,
            created_at=created_at.isoformat(timespec="seconds"),
        )

        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            logger.critical(f"写入需求文档失败: {exc}")
            return SkillResult(ok=False, output=f"写入需求文档失败: {exc}")

        logger.notice(f"已提交能力需求: {path}")

        notify_ok: bool | None = None
        notify_msg = ""
        if want_notify:
            bot = self.bot if self.bot is not None else FeishuBot()
            content = (
                f"**标题**: {title}\n"
                f"**优先级**: {priority}\n"
                f"**需要能力**: {need}\n"
                f"**为何不足**: {why}\n"
                f"**文档**: `{path}`\n"
                f"\n需求已记录，不会立即实现；Agent 将继续用现有 Skill 推进任务。"
            )
            resp = bot.send_markdown("SelfAgent 能力需求", content)
            notify_ok = resp.ok
            notify_msg = resp.msg
            if not resp.ok:
                logger.warning(f"飞书通知失败: {resp.msg}")
            else:
                logger.notice("飞书已通知能力需求")

        payload = {
            "title": title,
            "need": need,
            "why": why,
            "context": context,
            "workaround": workaround,
            "priority": priority,
            "path": str(path),
            "filename": filename,
            "notified": notify_ok,
            "notify_msg": notify_msg,
            "reminder": "需求不会立即实现，请继续用现有 Skill 完成当前任务。",
        }
        return SkillResult(
            ok=True,
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            data=payload,
        )


def _slugify(title: str, *, max_len: int = 40) -> str:
    text = _SLUG_RE.sub("-", title.strip()).strip("-_")
    if not text:
        text = "requirement"
    return text[:max_len].rstrip("-_")


def _render_markdown(
    *,
    title: str,
    need: str,
    why: str,
    context: str,
    workaround: str,
    priority: str,
    created_at: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- **状态**: 待评审（不会立即实现）",
        f"- **优先级**: {priority}",
        f"- **创建时间**: {created_at}",
        "",
        "## 需要的能力",
        "",
        need,
        "",
        "## 为何现有 Skill 不足",
        "",
        why,
        "",
    ]
    if context:
        lines.extend(["## 任务背景", "", context, ""])
    if workaround:
        lines.extend(["## 当前变通方案", "", workaround, ""])
    lines.extend(
        [
            "## 说明",
            "",
            "本需求由 Agent 在认定现有 Skill 无法满足时提交。",
            "文档落盘与飞书通知后，Agent 应继续用现有能力完成当前任务。",
            "",
        ]
    )
    return "\n".join(lines)
