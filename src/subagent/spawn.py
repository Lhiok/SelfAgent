"""run_subagent：隔离子循环 + sidechain 落盘。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from ai import AIClient, AIMessage
from agent.engine import AgentEngine
from log import get_logger
from permission import PermissionGuard
from session.control import RunControl
from session.mode import AgentMode
from skills.bridge import SkillBridge
from skills.registry import SkillRegistry
from subagent.context import inherit_context, summarize_result
from subagent.transcript import SubagentMeta, append_message, load_messages, write_meta

logger = get_logger("subagent.spawn")

ProgressFn = Callable[[dict[str, Any]], None]

RUN_SUBAGENT_TOOL = "run_subagent"


def default_session_root() -> Path:
    return Path("logs") / "sessions" / "_default"


def run_subagent(
    *,
    prompt: str,
    ai: AIClient,
    skills: SkillRegistry,
    permission: PermissionGuard,
    bridge: SkillBridge | None = None,
    description: str = "",
    max_steps: int = 8,
    agent_id: str | None = None,
    session_id: str = "",
    session_root: Path | None = None,
    workdir: Path | str | None = None,
    memory_text: str = "",
    parent_summary: str = "",
    on_progress: ProgressFn | None = None,
    mcp_manager: Any | None = None,
) -> dict[str, Any]:
    """
    启动子 Agent；返回 {ok, agent_id, answer, summary, path}。
    """
    root = session_root or default_session_root()
    if session_id:
        root = Path("logs") / "sessions" / session_id
    root.mkdir(parents=True, exist_ok=True)

    aid = (agent_id or "").strip() or uuid.uuid4().hex[:12]
    meta = SubagentMeta(
        agent_id=aid,
        parent_session_id=session_id,
        description=description or prompt[:80],
        workdir=str(workdir or ""),
    )
    write_meta(root, meta)

    if on_progress:
        try:
            on_progress(
                {
                    "type": "subagent_start",
                    "agent_id": aid,
                    "description": meta.description,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    try:
        from hooks import HookEvent, emit

        emit(
            HookEvent.SUBAGENT_START,
            payload={"agent_id": aid, "description": meta.description, "prompt": prompt},
        )
    except Exception:  # noqa: BLE001
        pass

    history = load_messages(root, aid)
    ctx = inherit_context(
        workdir=workdir,
        memory_text=memory_text,
        parent_summary=parent_summary,
    )
    task = prompt if not ctx else f"{ctx}\n\n---\n任务:\n{prompt}"

    append_message(root, aid, AIMessage(role="user", content=task))

    child_control = RunControl()
    # 子 Agent 使用独立 permission 副本语义：共享规则但独立 plan_active
    child_perm = permission

    def _child_progress(ev: dict[str, Any]) -> None:
        if on_progress:
            try:
                payload = dict(ev)
                payload["type"] = "subagent_progress"
                payload["agent_id"] = aid
                on_progress(payload)
            except Exception:  # noqa: BLE001
                pass

    engine = AgentEngine(
        ai=ai,
        skills=skills,
        permission=child_perm,
        bridge=bridge,
        control=child_control,
        on_progress=_child_progress,
        max_steps=max(1, int(max_steps)),
        workdir=Path(workdir) if workdir else None,
        mcp_manager=mcp_manager,
        session_id=session_id,
        include_run_subagent=False,
    )
    result = engine.submit(
        task if not history else prompt,
        history=history if history else None,
        mode=AgentMode.AGENT,
    )

    answer = result.answer or ""
    append_message(root, aid, AIMessage(role="assistant", content=answer))
    summary = summarize_result(answer)

    if on_progress:
        try:
            on_progress(
                {
                    "type": "subagent_stop",
                    "agent_id": aid,
                    "ok": result.completed,
                    "summary": summary,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    try:
        from hooks import HookEvent, emit

        emit(
            HookEvent.SUBAGENT_STOP,
            payload={
                "agent_id": aid,
                "ok": result.completed,
                "summary": summary,
            },
        )
    except Exception:  # noqa: BLE001
        pass

    from subagent.transcript import transcript_path

    return {
        "ok": bool(result.completed),
        "agent_id": aid,
        "answer": answer,
        "summary": summary,
        "stop_reason": result.stop_reason,
        "path": str(transcript_path(root, aid)),
    }
