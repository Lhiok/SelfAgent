"""连续对话：跨多轮保持上下文，持续执行任务。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as cfg
from ai import AIMessage, ToolCall
from log import get_logger, set_session_id
from agent.facade import Agent, AgentResult
from session.changes import collect_changes_from_steps
from memory import MemoryService, load_memory_config
from plan.lifecycle import PlanLifecycle
from plan.types import PlanPhase
from memory import compaction_settings
from session.mode import AgentMode
from plan.model import Plan
from skills.ask_user import collect_ask_answers_from_steps

logger = get_logger("session.conversation")

DEFAULT_PERSIST_DIR = Path("logs/sessions")

_CONTINUOUS_HINT = (
    "[连续对话] 请结合此前对话与已确认约束继续执行；"
    "若用户在推进同一任务，请复用已有结论，不要无故重头开始。"
)


@dataclass
class TurnRecord:
    index: int
    user: str
    answer: str
    completed: bool
    mode: str
    step_count: int = 0
    changes: list[dict[str, Any]] = field(default_factory=list)
    ask_answers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ConversationState:
    session_id: str
    messages: list[AIMessage] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)
    pending_plan: Plan | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Conversation:
    """
    多轮会话封装。

    - chat(): 带历史继续对话 / 执行任务
    - confirm_plan(): 执行上一轮产出的计划
    - reset() / save() / load() / resume() / list_sessions()
    """

    def __init__(
        self,
        agent: Agent | None = None,
        *,
        session_id: str | None = None,
        max_history_messages: int | None = None,
        inject_continuous_hint: bool | None = None,
        persist_dir: str | Path | None = None,
    ) -> None:
        react_cfg = cfg.get_section("react", {}) or {}
        conv_cfg = react_cfg.get("conversation") or {}

        self.agent = agent or Agent()
        self.session_id = session_id or str(uuid.uuid4())
        set_session_id(self.session_id)
        self.max_history_messages = (
            max_history_messages
            if max_history_messages is not None
            else int(conv_cfg.get("max_history_messages", 40))
        )
        mem_cfg = load_memory_config(conv_cfg)
        self.compact_after_messages = mem_cfg.compact_after_messages
        self.compact_keep_recent = mem_cfg.compact_keep_recent
        _, _, use_ai = compaction_settings(conv_cfg)
        self.compact_use_ai = use_ai
        self.inject_continuous_hint = (
            inject_continuous_hint
            if inject_continuous_hint is not None
            else bool(conv_cfg.get("inject_hint", True))
        )
        if persist_dir is not None:
            self.persist_dir = Path(persist_dir) if str(persist_dir).strip() else None
        else:
            raw = conv_cfg.get("persist_dir")
            self.persist_dir = Path(raw) if raw else DEFAULT_PERSIST_DIR

        self.messages: list[AIMessage] = []
        self.turns: list[TurnRecord] = []
        self.workflow_run_id: str = ""
        self.metadata: dict[str, Any] = {}
        self.last_save_path: Path | None = None
        wd = getattr(self.agent, "workdir", None) or Path.cwd()
        self.memory = MemoryService(
            wd,
            session_id=self.session_id,
            persist_dir=self.persist_dir or DEFAULT_PERSIST_DIR,
            config=mem_cfg,
        )
        self.agent.memory_service = self.memory
        self._wire_memory_skill()
        plan_cfg = cfg.get_section("plan", {}) or {}
        plans_dir = plan_cfg.get("plans_dir") or (
            (cfg.get_section("workflow", {}) or {}).get("plans_dir")
        )
        self.plan_lc = PlanLifecycle(plans_base=plans_dir or ".selfagent/plans")
        self.agent.plan_lifecycle = self.plan_lc

    @property
    def pending_plan(self) -> Plan | None:
        return self.plan_lc.pending_plan

    @pending_plan.setter
    def pending_plan(self, value: Plan | None) -> None:
        if value is None:
            if self.plan_lc.phase is not PlanPhase.EXECUTING:
                self.plan_lc.session.plan = None
                if self.plan_lc.phase is PlanPhase.AWAITING_CONFIRM:
                    self.plan_lc.session.phase = PlanPhase.IDLE
            return
        self.plan_lc.session.plan = value
        if value.ok and self.plan_lc.phase is PlanPhase.IDLE:
            self.plan_lc.session.phase = PlanPhase.AWAITING_CONFIRM

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def cancel(self) -> None:
        """请求取消当前正在执行的 turn。"""
        self.agent.control.cancel()

    def enqueue(self, text: str) -> None:
        """向当前 turn 队列中途补充用户消息（步间注入）。"""
        self.agent.control.enqueue(text)

    def compact_now(self, *, use_ai: bool = True) -> int:
        """手动压缩历史；返回压缩后的消息条数。"""
        before = len(self.messages)
        self.messages = self.memory.compact(
            self.messages,
            ai=self.agent.ai if use_ai else None,
            use_ai=use_ai,
            compact_after=1 if before > self.compact_keep_recent else 0,
            keep_recent=self.compact_keep_recent,
        )
        if self.persist_dir is not None:
            self.save()
        logger.notice(f"手动压缩会话: {before} -> {len(self.messages)}")
        return len(self.messages)

    def _wire_memory_skill(self) -> None:
        skill = self.agent.skills.get("memory_ops")
        if skill is None:
            return
        guard = self.memory.guard

        def _on_write() -> None:
            guard.mark_written()

        skill.on_write = _on_write  # type: ignore[attr-defined]
        if hasattr(skill, "set_workdir"):
            try:
                skill.set_workdir(self.memory.workdir)
            except Exception:  # noqa: BLE001
                pass

    def chat(self, user_input: str, *, mode: str | AgentMode | None = None) -> AgentResult:
        """发送一轮用户消息，基于历史继续执行。"""
        text = (user_input or "").strip()
        if not text:
            return AgentResult(answer="空输入，已忽略", completed=False, mode=self.agent.mode.value)

        # 若上一轮仍在跑，则入队而非开新 turn
        if self.agent.control.is_running:
            self.enqueue(text)
            return AgentResult(
                answer="已加入当前任务队列，将在下一步注入。",
                completed=False,
                mode=self.agent.mode.value,
                stop_reason="enqueued",
            )

        agent = self.agent.with_mode(mode) if mode is not None else self.agent
        # with_mode 新建实例时共享同一 control
        history = list(self.messages)
        if self.inject_continuous_hint and history:
            history = [
                *history,
                AIMessage(role="user", content=_CONTINUOUS_HINT),
            ]

        turn_no = self.turn_count + 1
        self._log_turn_separator(
            turn_no,
            mode=agent.mode.value,
            user=text,
            kind="chat",
        )
        try:
            from hooks import HookEvent, emit

            hook_result = emit(
                HookEvent.USER_PROMPT_SUBMIT,
                payload={"prompt": text, "mode": agent.mode.value},
            )
            if hook_result.extra_context:
                history = [
                    *history,
                    AIMessage(
                        role="user",
                        content=f"[hook_context]\n{hook_result.extra_context}",
                    ),
                ]
            if hook_result.blocked:
                return AgentResult(
                    answer=hook_result.reason or "被 UserPromptSubmit hook 拦截",
                    completed=False,
                    mode=agent.mode.value,
                    stop_reason="hook_blocked",
                )
        except Exception:  # noqa: BLE001
            pass
        if hasattr(agent, "set_session_id"):
            try:
                agent.set_session_id(self.session_id)
            except Exception:  # noqa: BLE001
                pass
        result = agent.run(text, history=history)
        self._ingest_result(user_input=text, result=result)
        if self.persist_dir is not None:
            self.save()
        return result
    def resume_after_ask(
        self,
        *,
        messages: list[AIMessage],
        observation: str,
        mode: str | AgentMode | None = None,
        user_task: str = "",
    ) -> AgentResult:
        """重启后根据检查点消息与用户回答继续本轮。"""
        run_mode = AgentMode.parse(mode) if mode is not None else self.agent.mode
        agent = self.agent.with_mode(run_mode)
        task = (user_task or "").strip() or "（续跑：用户已确认）"
        turn_no = self.turn_count + 1
        self._log_turn_separator(
            turn_no,
            mode=agent.mode.value,
            user=task,
            kind="resume_ask",
        )
        result = agent.continue_from_messages(
            messages,
            observation=observation,
            mode=agent.mode,
        )
        self._ingest_result(user_input=task, result=result)
        if self.persist_dir is not None:
            self.save()
        return result

    def confirm_plan(
        self,
        plan: Plan | None = None,
        *,
        user_note: str = "请按已确认计划执行",
        plan_hash: str | None = None,
    ) -> AgentResult:
        """确认并执行计划（唯一 WorkflowRun）。"""
        target = plan or self.pending_plan
        if target is None or not target.steps:
            return AgentResult(
                answer="没有可执行的计划，请先在 Plan Mode 下生成计划",
                completed=False,
                mode=AgentMode.AGENT.value,
            )

        expected = (
            plan_hash
            or self.plan_lc.session.plan_hash
            or self.metadata.get("pending_plan_hash")
        )
        try:
            wf_run = self.plan_lc.confirm(
                expected_hash=str(expected) if expected else None,
                plan=target,
            )
        except ValueError as exc:
            return AgentResult(
                answer=str(exc),
                completed=False,
                mode=AgentMode.AGENT.value,
                plan=target,
            )

        turn_no = self.turn_count + 1
        self._log_turn_separator(
            turn_no,
            mode=AgentMode.AGENT.value,
            user=user_note,
            kind="confirm_plan",
        )
        executor = self.agent.with_mode(AgentMode.AGENT)
        result = executor.execute_plan(
            target,
            history=list(self.messages),
            workflow_run=wf_run,
        )
        self.workflow_run_id = result.workflow_run_id or wf_run.id
        if result.completed:
            restore = self.plan_lc.finish_execution(success=True)
            self.agent = self.agent.with_mode(restore)
            self.metadata.pop("pending_plan_hash", None)
            self.metadata.pop("pending_plan_id", None)
        else:
            ans = result.answer or ""
            stale = (
                "未找到匹配的 old_text" in ans
                or "old_text 匹配到" in ans
                or "不是文件或不存在" in ans
            )
            if stale:
                self.plan_lc.finish_execution(success=True)
                self.plan_lc.cancel()
                result = replace(
                    result,
                    answer=ans + " 计划片段已失效，请重新 read 后生成计划再确认。",
                    plan=None,
                )
            else:
                self.plan_lc.finish_execution(success=False)
        self.messages.append(AIMessage(role="user", content=user_note))
        self.messages.append(
            AIMessage(
                role="assistant",
                content=f"Final Answer: {result.answer}",
            )
        )
        self._trim_history()
        self.turns.append(
            TurnRecord(
                index=len(self.turns) + 1,
                user=user_note,
                answer=result.answer,
                completed=result.completed,
                mode=AgentMode.AGENT.value,
                step_count=len(result.steps),
                changes=collect_changes_from_steps(result.steps),
                ask_answers=collect_ask_answers_from_steps(result.steps),
            )
        )
        if self.persist_dir is not None:
            self.save()
        return result

    def reject_plan(self, feedback: str = "") -> dict[str, Any]:
        """拒绝待确认计划，留在 Plan Mode 并注入反馈。"""
        try:
            self.plan_lc.reject(feedback)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        # 拒绝后保持/进入 PLAN，便于修订
        if self.agent.mode is not AgentMode.PLAN:
            self.set_mode(AgentMode.PLAN)
        else:
            self.agent.permission.set_plan_active(True)
        note = (feedback or "").strip()
        if note:
            self.messages.append(
                AIMessage(
                    role="user",
                    content=f"[计划已拒绝] {note}\n请修订后再次 submit_plan。",
                )
            )
        if self.persist_dir is not None:
            self.save()
        return {
            "ok": True,
            "phase": self.plan_lc.phase.value,
            "feedback": self.plan_lc.session.last_reject_feedback,
        }

    def reset(self) -> None:
        """清空会话历史与待执行计划。"""
        self.messages.clear()
        self.turns.clear()
        self.plan_lc.cancel()
        self.workflow_run_id = ""
        logger.notice(f"会话已重置: {self.session_id[:8]}")

    def set_mode(self, mode: str | AgentMode) -> None:
        parsed = AgentMode.parse(mode)
        if parsed is AgentMode.PLAN:
            pre = self.agent.mode.value
            self.plan_lc.enter(pre)
            self.agent = self.agent.with_mode(AgentMode.PLAN)
            self.agent.plan_lifecycle = self.plan_lc
            self.agent.permission.set_plan_active(True)
        else:
            self.agent = self.agent.with_mode(parsed)
            self.agent.plan_lifecycle = self.plan_lc
            if self.plan_lc.phase is PlanPhase.PLANNING:
                # 主动离开规划且未提交：回到 idle（保留 draft 若已 awaiting）
                pass
            if self.plan_lc.phase is not PlanPhase.AWAITING_CONFIRM:
                self.agent.permission.set_plan_active(False)
            else:
                # awaiting 仍禁止写入
                self.agent.permission.set_plan_active(True)

    def set_detail(self, level: str) -> None:
        """切换过程细节级别：off / summary / full。"""
        self.agent.set_detail(level)

    def set_workdir(self, workdir: str | Path):
        """设定 Agent 工作目录。"""
        path = self.agent.set_workdir(workdir)
        self.memory.set_workdir(path)
        self._wire_memory_skill()
        return path

    def state(self) -> ConversationState:
        return ConversationState(
            session_id=self.session_id,
            messages=list(self.messages),
            turns=list(self.turns),
            pending_plan=self.pending_plan,
            metadata={**dict(self.metadata), "workflow_run_id": self.workflow_run_id},
        )

    def save(self, path: str | Path | None = None) -> Path:
        """持久化会话到 JSON。"""
        target = Path(path) if path else self._default_persist_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.persist_dir is None:
            self.persist_dir = target.parent
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        preview = ""
        if self.turns:
            preview = self.turns[0].user.replace("\n", " ").strip()[:80]
        payload = {
            "session_id": self.session_id,
            "updated_at": now,
            "preview": preview,
            "workdir": str(self.agent.workdir) if getattr(self.agent, "workdir", None) else "",
            "messages": [_message_to_dict(m) for m in self.messages],
            "turns": [
                {
                    "index": t.index,
                    "user": t.user,
                    "answer": t.answer,
                    "completed": t.completed,
                    "mode": t.mode,
                    "step_count": t.step_count,
                    "changes": list(t.changes or []),
                    "ask_answers": list(t.ask_answers or []),
                }
                for t in self.turns
            ],
            "plan": self.plan_lc.to_dict(),
            "workflow_run_id": self.workflow_run_id,
            "memory": {
                "last_summarized_id": self.memory.session.last_summarized_id,
            },
            "metadata": self.metadata,
            "agent_mode": self.agent.mode.value,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_save_path = target
        logger.notice(f"会话已保存: {target}")
        return target

    def resume(self, path: str | Path) -> "Conversation":
        """从历史文件恢复到当前实例（沿用 load 后的 agent，含 workdir/mode）。"""
        loaded = Conversation.load(path, agent=self.agent)
        self.agent = loaded.agent
        self.session_id = loaded.session_id
        set_session_id(self.session_id)
        self.messages = loaded.messages
        self.turns = loaded.turns
        self.plan_lc = loaded.plan_lc
        self.workflow_run_id = loaded.workflow_run_id
        self.metadata = loaded.metadata
        self.memory = loaded.memory
        self.agent.memory_service = self.memory
        self.agent.plan_lifecycle = self.plan_lc
        self._wire_memory_skill()
        self.last_save_path = Path(path).resolve()
        if self.persist_dir is None:
            self.persist_dir = self.last_save_path.parent
        elif loaded.persist_dir is not None:
            self.persist_dir = loaded.persist_dir
        logger.notice(
            f"已从历史恢复: {self.last_save_path} "
            f"(session={self.session_id[:8]}, turns={self.turn_count})"
        )
        return self

    @classmethod
    def resolve_session_path(
        cls,
        ref: str,
        *,
        persist_dir: str | Path | None = None,
    ) -> Path:
        """
        解析会话引用：文件路径 / session_id / 前缀 / latest。
        """
        text = (ref or "").strip()
        if not text:
            raise FileNotFoundError("未指定会话")

        direct = Path(text).expanduser()
        if direct.is_file():
            return direct.resolve()

        base = cls._resolve_persist_dir(persist_dir)
        if text.lower() in {"latest", "last", "-"}:
            items = cls.list_sessions(base)
            if not items:
                raise FileNotFoundError(f"目录中没有可恢复的会话: {base}")
            return Path(items[0]["path"])

        exact = base / f"{text}.json"
        if exact.is_file():
            return exact.resolve()

        # 前缀匹配 session_id
        matches = sorted(base.glob(f"{text}*.json"))
        if len(matches) == 1:
            return matches[0].resolve()
        if len(matches) > 1:
            raise FileNotFoundError(
                f"会话前缀 {text!r} 匹配到多个文件，请写更长一点: "
                + ", ".join(p.stem[:12] for p in matches[:5])
            )
        raise FileNotFoundError(f"未找到会话: {text}（目录 {base}）")

    @classmethod
    def list_sessions(
        cls,
        persist_dir: str | Path | None = None,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列出历史会话（按更新时间倒序）。"""
        base = cls._resolve_persist_dir(persist_dir)
        if not base.is_dir():
            return []

        items: list[dict[str, Any]] = []
        for path in base.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            turns = data.get("turns") or []
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            items.append(
                {
                    "path": str(path.resolve()),
                    "session_id": str(data.get("session_id") or path.stem),
                    "turn_count": len(turns),
                    "preview": str(data.get("preview") or (turns[0].get("user") if turns else "") or "")[:80],
                    "workdir": str(data.get("workdir") or ""),
                    "updated_at": str(data.get("updated_at") or mtime.isoformat(timespec="seconds")),
                    "mtime": mtime.timestamp(),
                }
            )
        items.sort(key=lambda x: x["mtime"], reverse=True)
        if limit > 0:
            items = items[:limit]
        for item in items:
            item.pop("mtime", None)
        return items

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        agent: Agent | None = None,
        persist_dir: str | Path | None = None,
    ) -> "Conversation":
        target = Path(path).expanduser().resolve()
        data = json.loads(target.read_text(encoding="utf-8"))
        save_dir = persist_dir if persist_dir is not None else target.parent
        conv = cls(
            agent=agent,
            session_id=str(data.get("session_id") or uuid.uuid4()),
            persist_dir=save_dir,
        )
        conv.messages = [_message_from_dict(m) for m in data.get("messages") or []]
        conv.turns = [
            TurnRecord(
                index=int(t.get("index") or i),
                user=str(t.get("user") or ""),
                answer=str(t.get("answer") or ""),
                completed=bool(t.get("completed")),
                mode=str(t.get("mode") or AgentMode.AGENT.value),
                step_count=int(t.get("step_count") or 0),
                changes=[
                    dict(c)
                    for c in (t.get("changes") or [])
                    if isinstance(c, dict)
                ],
                ask_answers=[
                    dict(a)
                    for a in (t.get("ask_answers") or [])
                    if isinstance(a, dict)
                ],
            )
            for i, t in enumerate(data.get("turns") or [], start=1)
        ]
        plan_block = data.get("plan") if isinstance(data.get("plan"), dict) else None
        if plan_block:
            conv.plan_lc.load_dict(plan_block)
        else:
            plan_data = data.get("pending_plan")
            if plan_data:
                from plan.model import PlanStep

                legacy = Plan(
                    summary=str(plan_data.get("summary") or ""),
                    thought=str(plan_data.get("thought") or ""),
                    steps=[
                        PlanStep(
                            index=int(s.get("index") or i),
                            skill=str(s.get("skill") or ""),
                            arguments=dict(s.get("arguments") or {}),
                            why=str(s.get("why") or ""),
                            raw_input=str(s.get("raw_input") or ""),
                        )
                        for i, s in enumerate(plan_data.get("steps") or [], start=1)
                    ],
                )
                meta = dict(data.get("metadata") or {})
                conv.plan_lc.restore_awaiting_plan(
                    legacy,
                    plan_id=str(meta.get("pending_plan_id") or ""),
                    plan_hash_value=str(meta.get("pending_plan_hash") or ""),
                    pre_mode=str(data.get("agent_mode") or "agent"),
                )
        conv.agent.plan_lifecycle = conv.plan_lc
        conv.metadata = dict(data.get("metadata") or {})
        conv.workflow_run_id = str(
            data.get("workflow_run_id")
            or conv.metadata.get("workflow_run_id")
            or ""
        )
        mem_meta = data.get("memory") if isinstance(data.get("memory"), dict) else {}
        conv.memory.session.last_summarized_id = str(
            mem_meta.get("last_summarized_id") or ""
        )
        mode = data.get("agent_mode")
        if mode:
            # 恢复模式时不要再次 enter 清空 phase
            parsed = AgentMode.parse(mode)
            conv.agent = conv.agent.with_mode(parsed)
            conv.agent.plan_lifecycle = conv.plan_lc
            if (
                parsed is AgentMode.PLAN
                or conv.plan_lc.phase is PlanPhase.AWAITING_CONFIRM
            ):
                conv.agent.permission.set_plan_active(True)
        workdir = str(data.get("workdir") or "").strip()
        if workdir:
            try:
                conv.set_workdir(workdir)
            except (OSError, ValueError) as exc:
                logger.warning(f"恢复工作目录失败 ({workdir}): {exc}")
        conv.last_save_path = target
        logger.notice(
            f"会话已加载: {target} (session={conv.session_id[:8]}, turns={conv.turn_count})"
        )
        return conv

    @classmethod
    def _resolve_persist_dir(cls, persist_dir: str | Path | None = None) -> Path:
        if persist_dir is not None and str(persist_dir).strip():
            return Path(persist_dir).expanduser().resolve()
        react_cfg = cfg.get_section("react", {}) or {}
        conv_cfg = react_cfg.get("conversation") or {}
        raw = conv_cfg.get("persist_dir")
        return Path(raw).expanduser().resolve() if raw else DEFAULT_PERSIST_DIR.resolve()

    def _log_turn_separator(
        self,
        turn_no: int,
        *,
        mode: str,
        user: str,
        kind: str = "chat",
    ) -> None:
        preview = user.replace("\n", " ").strip()
        if len(preview) > 120:
            preview = preview[:119] + "…"
        bar = "=" * 72
        logger.notice(
            f"\n{bar}\n"
            f"  第 {turn_no} 轮 | session={self.session_id[:8]} | "
            f"mode={mode} | {kind}\n"
            f"  用户: {preview}\n"
            f"{bar}"
        )

    def _ingest_result(self, *, user_input: str, result: AgentResult) -> None:
        # 用本轮完整非 system 消息替换历史，天然包含此前上下文
        merged = [m for m in result.messages if m.role != "system"]
        if not merged:
            merged = list(self.messages)
            merged.append(AIMessage(role="user", content=user_input))
            merged.append(AIMessage(role="assistant", content=result.answer))
        self.messages = merged
        self._trim_history()

        if result.plan is not None and result.plan.ok:
            try:
                if self.plan_lc.phase is PlanPhase.IDLE:
                    self.plan_lc.enter(self.agent.mode.value)
                self.plan_lc.submit(result.plan)
                # 提交后仍只读，等待 confirm
                self.agent.permission.set_plan_active(True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"计划提交/落盘失败: {exc}")
        if result.workflow_run_id:
            self.workflow_run_id = result.workflow_run_id

        self.turns.append(
            TurnRecord(
                index=len(self.turns) + 1,
                user=user_input,
                answer=result.answer,
                completed=result.completed,
                mode=result.mode,
                step_count=len(result.steps),
                changes=collect_changes_from_steps(result.steps),
                ask_answers=collect_ask_answers_from_steps(result.steps),
            )
        )
        self._schedule_memory_turn_end(user=user_input, answer=result.answer or "")

    def _trim_history(self) -> None:
        if self.compact_after_messages > 0:
            # 热路径默认不用 AI；优先 session notes（memory.compact）
            self.messages = self.memory.compact(
                self.messages,
                ai=self.agent.ai if self.compact_use_ai else None,
                use_ai=self.compact_use_ai,
                compact_after=self.compact_after_messages,
                keep_recent=self.compact_keep_recent,
            )

        limit = self.max_history_messages
        if limit <= 0 or len(self.messages) <= limit:
            return
        # 保留最近 N 条；尽量从 user 消息边界切开
        trimmed = self.messages[-limit:]
        while trimmed and trimmed[0].role != "user":
            trimmed = trimmed[1:]
        self.messages = trimmed
        logger.notice(f"会话历史已裁剪至 {len(self.messages)} 条")

    def _schedule_memory_turn_end(self, *, user: str, answer: str) -> None:
        """后台更新 session notes + extract，不挡 UI。"""
        import threading

        msgs = list(self.messages)

        def _job() -> None:
            try:
                self.memory.on_turn_end(
                    user=user, answer=answer, messages=msgs
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"memory turn_end failed: {exc}")

        threading.Thread(
            target=_job, name="memory-turn-end", daemon=True
        ).start()

    def _default_persist_path(self) -> Path:
        base = self.persist_dir or DEFAULT_PERSIST_DIR
        return Path(base) / f"{self.session_id}.json"


def _message_to_dict(m: AIMessage) -> dict:
    payload: dict = {
        "role": m.role,
        "content": m.content,
        "name": m.name,
    }
    if m.tool_call_id:
        payload["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        payload["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in m.tool_calls
        ]
    return payload


def _message_from_dict(m: dict) -> AIMessage:
    tool_calls = []
    for tc in m.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        tool_calls.append(
            ToolCall(
                id=str(tc.get("id") or ""),
                name=str(tc.get("name") or ""),
                arguments=str(tc.get("arguments") or "{}"),
            )
        )
    return AIMessage(
        role=m.get("role", "user"),
        content=str(m.get("content") or ""),
        name=m.get("name"),
        tool_call_id=m.get("tool_call_id"),
        tool_calls=tool_calls,
    )
