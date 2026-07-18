"""SelfAgent CLI：终端连续对话。"""

from __future__ import annotations

import argparse
from pathlib import Path

import config as cfg
from log import get_run_log_path, reset_logger
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import SkillRegistry


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SelfAgent CLI 连续对话")
    parser.add_argument(
        "--workdir",
        "-C",
        default=None,
        help="Agent 工作目录（文件/搜索/命令/Git 的 root）",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认：仓库根目录 config.yaml）",
    )
    parser.add_argument(
        "--resume",
        "-r",
        default=None,
        metavar="REF",
        help="从历史恢复：路径 / session_id / 前缀 / latest",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="列出可恢复的历史会话后退出",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    cfg_path = Path(args.config) if args.config else root / "config.yaml"
    if cfg_path.is_file():
        cfg.load_config(cfg_path)
    reset_logger()

    if args.list_sessions:
        _print_sessions(Conversation.list_sessions())
        return

    agent = ReActAgent(
        skills=SkillRegistry.from_config(workdir=args.workdir),
        permission=PermissionGuard.from_config(),
        mode=AgentMode.AGENT,
        workdir=args.workdir,
    )
    conv = Conversation(agent)

    if args.resume:
        try:
            path = Conversation.resolve_session_path(args.resume)
            conv.resume(path)
        except (OSError, FileNotFoundError, ValueError) as exc:
            print(f"恢复失败: {exc}")
            return
        print(f"已恢复会话 {conv.session_id[:8]}，共 {conv.turn_count} 轮")
        if conv.turns:
            last = conv.turns[-1]
            print(f"上一轮用户: {last.user[:120]}")
            print(f"上一轮助手: {last.answer[:200]}")
        if conv.pending_plan and conv.pending_plan.ok:
            print("--- 待确认计划（可用 /confirm）---")
            print(conv.pending_plan.format_text())

    print(
        "SelfAgent CLI。命令: /plan /agent /confirm /detail /workdir "
        "/sessions /load /save /reset /quit"
    )
    print(f"当前细节级别: {agent.detail}（可用 /detail off|summary|full）")
    print(f"工作目录: {agent.workdir}（可用 /workdir <路径>）")
    print(f"会话: {conv.session_id[:8]}（自动保存到 {conv.persist_dir}）")
    run_log = get_run_log_path()
    if run_log is not None:
        print(f"本次运行日志: {run_log}")

    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            if conv.turn_count and conv.persist_dir is not None:
                path = conv.save()
                print(f"\n会话已保存: {path}")
            print("再见")
            break
        if not text:
            continue
        if text in {"/quit", "/exit", "quit", "exit"}:
            if conv.turn_count and conv.persist_dir is not None:
                path = conv.save()
                print(f"会话已保存: {path}")
            break
        if text == "/reset":
            conv.reset()
            print("会话已清空（session_id 不变，继续写入同一文件）")
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
        if text == "/workdir" or text.startswith("/workdir "):
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                print(f"当前工作目录: {conv.agent.workdir}")
                print("用法: /workdir <路径>")
                continue
            try:
                path = conv.set_workdir(parts[1])
            except (OSError, ValueError) as exc:
                print(f"切换失败: {exc}")
                continue
            print(f"已切换工作目录: {path}")
            continue
        if text == "/sessions":
            _print_sessions(Conversation.list_sessions(conv.persist_dir))
            continue
        if text == "/load" or text.startswith("/load "):
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                print("用法: /load <路径|session_id|前缀|latest>")
                continue
            try:
                path = Conversation.resolve_session_path(
                    parts[1], persist_dir=conv.persist_dir
                )
                conv.resume(path)
            except (OSError, FileNotFoundError, ValueError) as exc:
                print(f"加载失败: {exc}")
                continue
            print(f"已加载 {conv.session_id[:8]}，共 {conv.turn_count} 轮")
            print(f"工作目录: {conv.agent.workdir}")
            if conv.pending_plan and conv.pending_plan.ok:
                print("--- 待确认计划 ---")
                print(conv.pending_plan.format_text())
            continue
        if text == "/save" or text.startswith("/save "):
            parts = text.split(maxsplit=1)
            try:
                path = conv.save(parts[1] if len(parts) > 1 else None)
            except OSError as exc:
                print(f"保存失败: {exc}")
                continue
            print(f"已保存: {path}")
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


def _print_sessions(items: list) -> None:
    if not items:
        print("（没有历史会话）")
        return
    print(f"共 {len(items)} 条历史会话：")
    for i, item in enumerate(items, start=1):
        sid = str(item["session_id"])[:8]
        print(
            f"  {i}. {sid}  turns={item['turn_count']}  "
            f"{item['updated_at']}\n"
            f"     preview: {item['preview'] or '(空)'}\n"
            f"     path: {item['path']}"
        )


def _print_result(result, *, streamed: bool) -> None:
    if result.detail_text and not streamed:
        print(result.detail_text)
    print(f"助手> {result.answer}")


if __name__ == "__main__":
    main()
