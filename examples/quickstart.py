"""快速体验各层能力（需先在 config.yaml 各模块节中配置）。"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg
from log import get_logger
from feishu import FeishuBot
from ai import create_ai_client
from session import Agent
from skills import SkillRegistry


def main() -> None:
    cfg.load_config(ROOT / "config.yaml")
    log = get_logger("example")

    ai_cfg = (cfg.get_section("ai", {}) or {}).get("deepseek") or {}
    model = ai_cfg.get("model") or "deepseek-chat"
    api_key = str(ai_cfg.get("api_key") or "")
    log.notice(f"ai.deepseek.model = {model!r}")

    # 日志层
    log.notice("这是一条提醒")
    log.warning("这是一条警告")

    # 飞书层
    bot = FeishuBot()
    if bot.webhook_url:
        bot.send_text("SelfAgent 快速示例：飞书推送正常")
    else:
        log.warning("未配置 feishu.webhook_url，跳过飞书推送")

    # AI + Agent + Skill
    if not api_key:
        log.warning("未配置 ai.deepseek.api_key，跳过 AI/Agent 示例")
        return

    agent = Agent(
        ai=create_ai_client("deepseek"),
        skills=SkillRegistry.from_config(),
        max_steps=8,
    )
    result = agent.run("列出当前项目根目录下的文件，并简要说明这是什么项目。")
    log.notice(f"Agent completed={result.completed} detail={result.detail_level}")
    # 未开 stream_detail 时，结束后仍可打印 result.detail_text / result.format_detail()
    if result.detail_text and not agent.stream_detail:
        print("\n" + result.detail_text)
    print("\n===== Final Answer =====\n")
    print(result.answer)


if __name__ == "__main__":
    main()
