"""SelfAgent CLI：终端连续对话（进度 / 取消 / 插话 / 会话管理）。"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

import config as cfg
from cli.progress import ProgressPrinter
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
    parser.add_argument(
        "--no-stream-delta",
        action="store_true",
        help="关闭助手流式 delta 打印（仍显示 step/skill 进度）",
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

    progress = ProgressPrinter(show_delta=not args.no_stream_delta)
    agent = ReActAgent(
        skills=SkillRegistry.from_config(workdir=args.workdir),
        permission=PermissionGuard.from_config(),
        mode=AgentMode.AGENT,
        workdir=args.workdir,
        on_progress=progress,
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
        _print_pending_plan(conv)

    print(
        "SelfAgent CLI。命令: /plan /agent /confirm /reject /detail /workdir "
        "/sessions /load /save /compact /status /reset /quit"
    )
    print("运行中: Ctrl+C 取消本轮；再按一次退出。可输入文字回车做中途补充（Windows）。")
    _print_status(conv)
    run_log = get_run_log_path()
    if run_log is not None:
        print(f"本次运行日志: {run_log}")

    interrupt_armed = False

    while True:
        try:
            text = input("\n你> ").strip()
            interrupt_armed = False
        except EOFError:
            _save_on_exit(conv)
            print("再见")
            break
        except KeyboardInterrupt:
            if interrupt_armed:
                _save_on_exit(conv)
                print("\n再见")
                break
            interrupt_armed = True
            print("\n（再按 Ctrl+C 退出；或继续输入）")
            continue

        if not text:
            continue
        if text in {"/quit", "/exit", "quit", "exit"}:
            _save_on_exit(conv)
            break
        if text == "/reset":
            conv.reset()
            print("会话已清空（session_id 不变，继续写入同一文件）")
            continue
        if text == "/status":
            _print_status(conv)
            continue
        if text == "/plan":
            conv.set_mode(AgentMode.PLAN)
            print("已切换 Plan Mode（先规划，不写入）")
            continue
        if text == "/agent":
            conv.set_mode(AgentMode.AGENT)
            print("已切换 Agent Mode")
            continue
        if text == "/reject":
            if conv.pending_plan is None:
                print("没有待确认计划")
                continue
            conv.pending_plan = None
            print("已丢弃待确认计划")
            continue
        if text == "/compact":
            n = conv.compact_now(use_ai=True)
            print(f"已压缩历史，当前 {n} 条消息")
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
            _print_pending_plan(conv)
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
            if not (conv.pending_plan and conv.pending_plan.ok):
                print("没有可确认的计划。先 /plan 生成，或查看 /status。")
                continue
            print("--- 将执行以下计划 ---")
            print(conv.pending_plan.format_text())
            print("执行中…（Ctrl+C 可取消）")
            try:
                result = _run_turn(conv, kind="confirm", progress=progress)
            except Exception as exc:  # noqa: BLE001
                progress.close_delta()
                print(f"助手> 执行计划失败: {exc}")
                continue
            progress.close_delta()
            _print_result(result, streamed=conv.agent.stream_detail)
            continue

        print(_status_line(conv) + "  执行中…")
        try:
            result = _run_turn(conv, user_text=text, progress=progress)
        except Exception as exc:  # noqa: BLE001
            progress.close_delta()
            print(f"助手> 本轮失败（会话仍可继续）: {exc}")
            continue
        progress.close_delta()
        if result is not None and result.stop_reason == "enqueued":
            print(f"助手> {result.answer}")
            continue
        _print_result(result, streamed=conv.agent.stream_detail)
        if result is not None and result.plan and result.plan.ok:
            print("--- 待确认计划（/confirm 执行，/reject 丢弃）---")
            print(result.plan.format_text())


def _run_turn(
    conv: Conversation,
    *,
    user_text: str | None = None,
    kind: str = "chat",
    progress: ProgressPrinter,
):
    """后台执行 turn；主线程轮询取消与（Windows）中途补充。"""
    result_box: dict = {}
    error_box: dict = {}
    done = threading.Event()

    def worker() -> None:
        try:
            if kind == "confirm":
                result_box["result"] = conv.confirm_plan()
            else:
                result_box["result"] = conv.chat(user_text or "")
        except Exception as exc:  # noqa: BLE001
            error_box["exc"] = exc
        finally:
            done.set()

    t = threading.Thread(target=worker, name="selfagent-turn", daemon=True)
    t.start()
    print("  （Ctrl+C 取消；Windows 下可键入补充后回车）", flush=True)

    try:
        while not done.is_set():
            for line in _poll_stdin_lines():
                text = line.strip()
                if not text:
                    continue
                if text in {"/cancel", "/stop"}:
                    conv.cancel()
                    print("  ! 已请求取消…", flush=True)
                else:
                    conv.enqueue(text)
                    print(f"  + 已排队补充: {text[:80]}", flush=True)
            # 兼容非 Windows：无 kbhit 时仅等待
            done.wait(0.15)
    except KeyboardInterrupt:
        conv.cancel()
        print("\n  ! Ctrl+C：已请求取消本轮…", flush=True)
        done.wait(timeout=120)

    t.join(timeout=120)
    progress.close_delta()

    if "exc" in error_box:
        raise error_box["exc"]
    return result_box.get("result")


def _poll_stdin_lines() -> list[str]:
    """非阻塞读取完整行；Windows 用 msvcrt，其它平台暂不抢占 stdin。"""
    if sys.platform != "win32":
        return []
    try:
        import msvcrt
    except ImportError:
        return []

    lines: list[str] = []
    # 模块级缓冲
    buf = getattr(_poll_stdin_lines, "_buf", "")
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            # 功能键前缀，丢弃后续
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\r", "\n"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            lines.append(buf)
            buf = ""
            continue
        if ch in ("\b", "\x08"):
            if buf:
                buf = buf[:-1]
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue
        buf += ch
        sys.stdout.write(ch)
        sys.stdout.flush()
    _poll_stdin_lines._buf = buf  # type: ignore[attr-defined]
    return lines


def _save_on_exit(conv: Conversation) -> None:
    if conv.turn_count and conv.persist_dir is not None:
        try:
            path = conv.save()
            print(f"\n会话已保存: {path}")
        except OSError as exc:
            print(f"\n保存失败: {exc}")


def _status_line(conv: Conversation) -> str:
    agent = conv.agent
    return (
        f"[{agent.mode.value} | steps≤{agent.max_steps} | "
        f"detail={agent.detail} | session={conv.session_id[:8]} | "
        f"workdir={agent.workdir}]"
    )


def _print_status(conv: Conversation) -> None:
    print(_status_line(conv))
    print(f"轮次: {conv.turn_count}  历史消息: {len(conv.messages)}")
    if conv.pending_plan and conv.pending_plan.ok:
        print("待确认计划: 是（/confirm 或 /reject）")
    else:
        print("待确认计划: 否")


def _print_pending_plan(conv: Conversation) -> None:
    if conv.pending_plan and conv.pending_plan.ok:
        print("--- 待确认计划（/confirm 执行，/reject 丢弃）---")
        print(conv.pending_plan.format_text())


def _print_sessions(items: list) -> None:
    if not items:
        print("（没有历史会话）")
        return
    print(f"共 {len(items)} 条历史会话：")
    print(f"{'#':>3}  {'id':<10}  {'turns':>5}  {'updated':<20}  preview")
    print("-" * 72)
    for i, item in enumerate(items, start=1):
        sid = str(item["session_id"])[:8]
        preview = (item.get("preview") or "(空)").replace("\n", " ")
        if len(preview) > 36:
            preview = preview[:35] + "…"
        print(
            f"{i:>3}  {sid:<10}  {item['turn_count']:>5}  "
            f"{str(item.get('updated_at') or ''):<20}  {preview}"
        )
        print(f"     path: {item['path']}")


def _print_result(result, *, streamed: bool) -> None:
    if result is None:
        print("助手> （无结果）")
        return
    if result.detail_text and not streamed:
        print(result.detail_text)
    suffix = f"  ({result.stop_reason})" if result.stop_reason else ""
    print(f"助手> {result.answer}{suffix}")


if __name__ == "__main__":
    main()
