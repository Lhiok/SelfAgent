"""连续对话：跨多轮保持上下文，持续执行任务。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config as cfg
from ai import AIMessage
from log import get_logger
from react.agent import ReActAgent, ReActResult
from react.mode import AgentMode
from react.plan import Plan

logger = get_logger("react.conversation")

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
    - reset() / save() / load()
    """

    def __init__(
        self,
        agent: ReActAgent | None = None,
        *,
        session_id: str | None = None,
        max_history_messages: int | None = None,
        inject_continuous_hint: bool | None = None,
        persist_dir: str | Path | None = None,
    ) -> None:
        react_cfg = cfg.get_section("react", {}) or {}
        conv_cfg = react_cfg.get("conversation") or {}

        self.agent = agent or ReActAgent()
        self.session_id = session_id or str(uuid.uuid4())
        self.max_history_messages = (
            max_history_messages
            if max_history_messages is not None
            else int(conv_cfg.get("max_history_messages", 40))
        )
        self.inject_continuous_hint = (
            inject_continuous_hint
            if inject_continuous_hint is not None
            else bool(conv_cfg.get("inject_hint", True))
        )
        configured_dir = persist_dir if persist_dir is not None else conv_cfg.get("persist_dir")
        self.persist_dir = Path(configured_dir) if configured_dir else None

        self.messages: list[AIMessage] = []
        self.turns: list[TurnRecord] = []
        self.pending_plan: Plan | None = None
        self.metadata: dict[str, Any] = {}

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def chat(self, user_input: str, *, mode: str | AgentMode | None = None) -> ReActResult:
        """发送一轮用户消息，基于历史继续执行。"""
        text = (user_input or "").strip()
        if not text:
            return ReActResult(answer="空输入，已忽略", completed=False, mode=self.agent.mode.value)

        agent = self.agent.with_mode(mode) if mode is not None else self.agent
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
        result = agent.run(text, history=history)
        self._ingest_result(user_input=text, result=result)
        if self.persist_dir is not None:
            self.save()
        return result

    def confirm_plan(
        self,
        plan: Plan | None = None,
        *,
        user_note: str = "请按已确认计划执行",
    ) -> ReActResult:
        """执行待确认计划（默认取 pending_plan），并写入对话历史。"""
        target = plan or self.pending_plan
        if target is None or not target.steps:
            return ReActResult(
                answer="没有可执行的计划，请先在 Plan Mode 下生成计划",
                completed=False,
                mode=AgentMode.AGENT.value,
            )

        turn_no = self.turn_count + 1
        self._log_turn_separator(
            turn_no,
            mode=AgentMode.AGENT.value,
            user=user_note,
            kind="confirm_plan",
        )
        executor = self.agent.with_mode(AgentMode.AGENT)
        result = executor.execute_plan(target, history=list(self.messages))
        # 把执行过程记入对话，便于后续追问
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
            )
        )
        if result.completed:
            self.pending_plan = None
        else:
            self.pending_plan = target
        if self.persist_dir is not None:
            self.save()
        return result

    def reset(self) -> None:
        """清空会话历史与待执行计划。"""
        self.messages.clear()
        self.turns.clear()
        self.pending_plan = None
        logger.notice(f"会话已重置: {self.session_id[:8]}")

    def set_mode(self, mode: str | AgentMode) -> None:
        self.agent = self.agent.with_mode(mode)

    def set_detail(self, level: str) -> None:
        """切换过程细节级别：off / summary / full。"""
        self.agent.set_detail(level)

    def state(self) -> ConversationState:
        return ConversationState(
            session_id=self.session_id,
            messages=list(self.messages),
            turns=list(self.turns),
            pending_plan=self.pending_plan,
            metadata=dict(self.metadata),
        )

    def save(self, path: str | Path | None = None) -> Path:
        """持久化会话到 JSON。"""
        target = Path(path) if path else self._default_persist_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self.session_id,
            "messages": [{"role": m.role, "content": m.content, "name": m.name} for m in self.messages],
            "turns": [
                {
                    "index": t.index,
                    "user": t.user,
                    "answer": t.answer,
                    "completed": t.completed,
                    "mode": t.mode,
                    "step_count": t.step_count,
                }
                for t in self.turns
            ],
            "pending_plan": self.pending_plan.to_dict() if self.pending_plan else None,
            "metadata": self.metadata,
            "agent_mode": self.agent.mode.value,
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.notice(f"会话已保存: {target}")
        return target

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        agent: ReActAgent | None = None,
    ) -> "Conversation":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        conv = cls(agent=agent, session_id=str(data.get("session_id") or uuid.uuid4()))
        conv.messages = [
            AIMessage(
                role=m.get("role", "user"),
                content=str(m.get("content") or ""),
                name=m.get("name"),
            )
            for m in data.get("messages") or []
        ]
        conv.turns = [
            TurnRecord(
                index=int(t.get("index") or i),
                user=str(t.get("user") or ""),
                answer=str(t.get("answer") or ""),
                completed=bool(t.get("completed")),
                mode=str(t.get("mode") or AgentMode.AGENT.value),
                step_count=int(t.get("step_count") or 0),
            )
            for i, t in enumerate(data.get("turns") or [], start=1)
        ]
        plan_data = data.get("pending_plan")
        if plan_data:
            from react.plan import PlanStep

            conv.pending_plan = Plan(
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
        conv.metadata = dict(data.get("metadata") or {})
        mode = data.get("agent_mode")
        if mode:
            conv.set_mode(mode)
        return conv

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

    def _ingest_result(self, *, user_input: str, result: ReActResult) -> None:
        # 用本轮完整非 system 消息替换历史，天然包含此前上下文
        merged = [m for m in result.messages if m.role != "system"]
        if not merged:
            merged = list(self.messages)
            merged.append(AIMessage(role="user", content=user_input))
            merged.append(AIMessage(role="assistant", content=result.answer))
        self.messages = merged
        self._trim_history()

        if result.plan is not None and result.plan.ok:
            self.pending_plan = result.plan

        self.turns.append(
            TurnRecord(
                index=len(self.turns) + 1,
                user=user_input,
                answer=result.answer,
                completed=result.completed,
                mode=result.mode,
                step_count=len(result.steps),
            )
        )

    def _trim_history(self) -> None:
        limit = self.max_history_messages
        if limit <= 0 or len(self.messages) <= limit:
            return
        # 保留最近 N 条；尽量从 user 消息边界切开
        trimmed = self.messages[-limit:]
        while trimmed and trimmed[0].role != "user":
            trimmed = trimmed[1:]
        self.messages = trimmed
        logger.notice(f"会话历史已裁剪至 {len(self.messages)} 条")

    def _default_persist_path(self) -> Path:
        base = self.persist_dir or Path("logs/sessions")
        return base / f"{self.session_id}.json"
