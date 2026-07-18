"""连续对话示例：多轮输入执行任务。"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg
from log import get_run_log_path, reset_logger
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import SkillRegistry


def main() -> None:
    cfg.load_config(ROOT / "config.yaml")
    reset_logger()  # 确保按当前配置生成本次运行日志文件
    agent = ReActAgent(
        skills=SkillRegistry.from_config(),
        permission=PermissionGuard.from_config(),
        mode=AgentMode.AGENT,
    )
    conv = Conversation(agent)
    print("连续对话已启动。命令: /plan /agent /confirm /detail /reset /quit")
    print(f"当前细节级别: {agent.detail}（可用 /detail off|summary|full）")
    run_log = get_run_log_path()
    if run_log is not None:
        print(f"本次运行日志: {run_log}")
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
        if text == "/detail" or text.startswith("/detail "):
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                print(f"当前细节级别: {conv.agent.detail}")
                print("用法: /detail off|summary|full")
                continue
            try:
                conv.set_detail(parts[1])
            except ValueError as exc:
                print(exc)
                continue
            print(f"已切换细节级别: {conv.agent.detail}")
            continue
        if text == "/confirm":
            try:
                result = conv.confirm_plan()
            except Exception as exc:  # noqa: BLE001
                print(f"助手> 执行计划失败: {exc}")
                continue
            _print_result(result, streamed=conv.agent.stream_detail)
            continue

        try:
            result = conv.chat(text)
        except Exception as exc:  # noqa: BLE001
            print(f"助手> 本轮失败（会话仍可继续）: {exc}")
            continue
        _print_result(result, streamed=conv.agent.stream_detail)
        if result.plan and result.plan.ok:
            print("--- 待确认计划 ---")
            print(result.plan.format_text())


def _print_result(result, *, streamed: bool) -> None:
    # 未实时流式输出时，结束后补打细节
    if result.detail_text and not streamed:
        print(result.detail_text)
    print(f"助手> {result.answer}")


if __name__ == "__main__":
    main()
