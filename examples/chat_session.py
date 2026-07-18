"""连续对话示例：多轮输入执行任务。"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import SkillRegistry


def main() -> None:
    cfg.load_config(ROOT / "config.yaml")
    agent = ReActAgent(
        skills=SkillRegistry.from_config(),
        permission=PermissionGuard.from_config(),
        mode=AgentMode.AGENT,
    )
    conv = Conversation(agent)
    print("连续对话已启动。命令: /plan /agent /confirm /reset /quit")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not text:
            continue
        if text in {"/quit", "/exit", "quit", "exit"}:
            break
        if text == "/reset":
            conv.reset()
            print("会话已清空")
            continue
        if text == "/plan":
            conv.set_mode(AgentMode.PLAN)
            print("已切换 Plan Mode")
            continue
        if text == "/agent":
            conv.set_mode(AgentMode.AGENT)
            print("已切换 Agent Mode")
            continue
        if text == "/confirm":
            result = conv.confirm_plan()
            print(f"助手> {result.answer}")
            continue

        result = conv.chat(text)
        print(f"助手> {result.answer}")
        if result.plan and result.plan.ok:
            print("--- 待确认计划 ---")
            print(result.plan.format_text())


if __name__ == "__main__":
    main()
