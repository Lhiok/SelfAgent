"""连续对话示例：多轮输入执行任务，支持从历史会话恢复。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as cfg
from log import get_run_log_path, reset_logger
from permission import PermissionGuard
from session import AgentMode, Conversation, Agent
from skills import SkillRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description="SelfAgent 连续对话")
    parser.add_argument(
        "--workdir",
        "-C",
        default=None,
        help="Agent 工作目录（文件/搜索/命令/Git 的 root）",
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
    args = parser.parse_args()

    cfg.load_config(ROOT / "config.yaml")
    reset_logger()

    if args.list_sessions:
        _print_sessions(Conversation.list_sessions())
        return

    guard = PermissionGuard.from_config()

    def _cli_permission_ask(payload: dict) -> bool:  # type: ignore[type-arg]
        skill = payload.get("skill") or "?"
        reason = payload.get("reason") or ""
        print(f"\n======== 需要授权 ========\n{reason}\nSkill: {skill}", flush=True)
        args = payload.get("arguments") or {}
        if args:
            print(f"参数: {args}", flush=True)
        print("允许执行？[y/N]", flush=True)
        try:
            raw = input("> ").strip().lower()
        except EOFError:
            raw = ""
        return raw in {"y", "yes", "是", "允许", "a", "allow"}

    guard.ask_handler = _cli_permission_ask
    agent = Agent(
        skills=SkillRegistry.from_config(permission=guard, load_permission=False, workdir=args.workdir),
        permission=guard,
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
        "连续对话已启动。命令: /plan /agent /confirm /reject <反馈> /detail /workdir "
        "/memory /remember <text> "
        "/workflows /run <name> /loop <sec> <prompt> "
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
            print(
                f"已切换 Plan Mode（phase={conv.plan_lc.phase.value}，"
                f"pre_mode={conv.plan_lc.session.pre_mode}）"
            )
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
            print(f"plan phase → {conv.plan_lc.phase.value}")
            continue
        if text == "/reject" or text.startswith("/reject "):
            fb = text[len("/reject") :].strip()
            out = conv.reject_plan(fb)
            if out.get("ok"):
                print(f"已拒绝计划，phase={out.get('phase')}，请修订后再次 submit_plan")
            else:
                print(f"拒绝失败: {out.get('error')}")
            continue
        if text == "/memory":
            summary = conv.memory.list_summary()
            print(f"memory dir: {summary.get('dir')}")
            print("--- MEMORY.md ---")
            print((summary.get("index") or "（空）")[:2000])
            topics = summary.get("topics") or []
            if topics:
                print("--- topics ---")
                for t in topics:
                    print(
                        f"  - {t.get('name')} [{t.get('type')}] "
                        f"{t.get('description')}"
                    )
            else:
                print("（无 topic）")
            continue
        if text.startswith("/remember "):
            note = text.split(maxsplit=1)[1].strip()
            if not note:
                print("用法: /remember <文本>")
                continue
            try:
                import uuid as _uuid

                from memory.types import MemoryType

                name = f"note-{_uuid.uuid4().hex[:8]}"
                ent = conv.memory.remember(
                    name, note, description=note[:80], mem_type=MemoryType.USER
                )
                print(f"已写入记忆: {ent.meta.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"写入失败: {exc}")
            continue
        if text == "/workflows":
            from workflow import WorkflowEngine

            engine = WorkflowEngine(
                conv.agent.bridge,
                conv.agent.permission,
                control=conv.agent.control,
            )
            cmds = [c for c in engine.registry.list() if c.kind == "workflow"]
            if not cmds:
                print("（未找到 YAML 工作流，请放到 .selfagent/workflows/）")
            else:
                for c in cmds:
                    print(f"  /{c.name}  {c.description}")
            continue
        if text.startswith("/run "):
            name = text.split(maxsplit=1)[1].strip()
            try:
                from workflow import WorkflowEngine

                engine = WorkflowEngine(
                    conv.agent.bridge,
                    conv.agent.permission,
                    control=conv.agent.control,
                    on_progress=conv.agent.on_progress,
                    agent_factory=lambda: conv.agent,
                )
                run = engine.run_def(name)
                conv.workflow_run_id = run.id
                if conv.persist_dir is not None:
                    conv.save()
                print(f"工作流 {name} → {run.status.value} (id={run.id})")
                for step in run.steps:
                    mark = "ok" if step.ok else "fail"
                    print(f"  [{mark}] {step.id}: {(step.output or '')[:200]}")
            except Exception as exc:  # noqa: BLE001
                print(f"运行失败: {exc}")
            continue
        if text.startswith("/loop "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                print("用法: /loop <秒> <提示词>")
                continue
            try:
                every = float(parts[1])
            except ValueError:
                print("用法: /loop <秒> <提示词>")
                continue
            prompt = parts[2].strip()
            from workflow.cron import CronScheduler

            sched = CronScheduler()
            task = sched.add(prompt, every_sec=every)
            print(
                f"已写入定时任务 {task.id}，每 {task.every_sec}s；"
                "需在配置中 workflow.cron_enabled=true 并启动引擎才会触发"
            )
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
