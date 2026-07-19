"""多工作目录 / 多会话持久化与懒加载 Conversation。"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai import AIMessage
from log import get_logger
from permission import PermissionGuard
from react import AgentMode, Conversation, ReActAgent
from skills import AskUserSkill, SkillRegistry

logger = get_logger("app.store")

DEFAULT_ROOT = Path("logs/workspaces")
ASK_USER_TIMEOUT_SEC = 1800.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _extract_user_task(messages: list[Any]) -> str:
    """从检查点消息中取本轮用户任务（跳过 Observation / 连续对话提示）。"""
    for item in reversed(messages or []):
        if isinstance(item, AIMessage):
            role, content = item.role, item.content or ""
        elif isinstance(item, dict):
            role, content = str(item.get("role") or ""), str(item.get("content") or "")
        else:
            continue
        if role != "user":
            continue
        text = content.strip()
        if not text or text.startswith("Observation"):
            continue
        if text.startswith("[连续对话]"):
            continue
        return text
    return "（续跑：用户已确认）"


class WorkspaceStore:
    """
    磁盘布局::

        logs/workspaces/
          index.json
          <workspace_id>/
            meta.json
            sessions/<session_id>.json
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or DEFAULT_ROOT).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._convs: dict[str, Conversation] = {}
        self._locks: dict[str, threading.Lock] = {}
        # session_id -> {ask_id, queue, event}
        self._pending_asks: dict[str, dict[str, Any]] = {}
        self._global = threading.Lock()
        self._ensure_index()

    # ---------- workspace ----------

    def list_workspaces(self, *, include_sessions: bool = True) -> list[dict[str, Any]]:
        index = self._read_index()
        items: list[dict[str, Any]] = []
        for wid in index.get("workspace_ids") or []:
            meta = self._read_meta(wid)
            if meta is None:
                continue
            item = self._workspace_public(meta)
            sessions = self.list_sessions(wid, include_archived=True)
            active = [s for s in sessions if not s.get("archived")]
            item["session_count"] = len(active)
            item["archived_count"] = len(sessions) - len(active)
            if include_sessions:
                item["sessions"] = sessions
            items.append(item)
        # 保持 index.workspace_ids 注册顺序，避免因会话活动更新 updated_at 而乱跳
        return items

    def add_workspace(self, path: str, *, title: str | None = None) -> dict[str, Any]:
        workdir = Path(path).expanduser().resolve()
        if not workdir.is_dir():
            raise NotADirectoryError(f"工作目录不存在或不是目录: {workdir}")

        for existing in self.list_workspaces(include_sessions=False):
            if Path(existing["path"]).resolve() == workdir:
                return existing

        wid = uuid.uuid4().hex[:12]
        now = _now_iso()
        meta = {
            "id": wid,
            "path": str(workdir),
            "title": (title or workdir.name or wid).strip() or wid,
            "collapsed": False,
            "created_at": now,
            "updated_at": now,
        }
        ws_dir = self.root / wid
        (ws_dir / "sessions").mkdir(parents=True, exist_ok=True)
        self._write_meta(wid, meta)
        index = self._read_index()
        ids = list(index.get("workspace_ids") or [])
        if wid not in ids:
            ids.append(wid)
        index["workspace_ids"] = ids
        self._write_index(index)
        logger.notice(f"工作区已添加: {wid} -> {workdir}")
        return self._workspace_public(meta)

    def remove_workspace(self, workspace_id: str) -> None:
        wid = self._require_workspace(workspace_id)["id"]
        # 释放内存会话
        with self._global:
            stale = [sid for sid, conv in self._convs.items() if conv.metadata.get("workspace_id") == wid]
            for sid in stale:
                self._convs.pop(sid, None)
                self._locks.pop(sid, None)
        index = self._read_index()
        index["workspace_ids"] = [x for x in (index.get("workspace_ids") or []) if x != wid]
        self._write_index(index)
        # 仅移除注册与本仓库元数据目录，不删除用户项目
        import shutil

        ws_dir = self.root / wid
        if ws_dir.is_dir():
            shutil.rmtree(ws_dir, ignore_errors=True)
        logger.notice(f"工作区已移除: {wid}")

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        meta = self._require_workspace(workspace_id)
        item = self._workspace_public(meta)
        sessions = self.list_sessions(workspace_id, include_archived=True)
        active = [s for s in sessions if not s.get("archived")]
        item["session_count"] = len(active)
        item["archived_count"] = len(sessions) - len(active)
        item["sessions"] = sessions
        return item

    def patch_workspace(
        self,
        workspace_id: str,
        *,
        title: str | None = None,
        collapsed: bool | None = None,
    ) -> dict[str, Any]:
        meta = self._require_workspace(workspace_id)
        if title is not None:
            meta["title"] = title.strip() or meta.get("title") or meta["id"]
        if collapsed is not None:
            meta["collapsed"] = bool(collapsed)
        meta["updated_at"] = _now_iso()
        self._write_meta(workspace_id, meta)
        return self.get_workspace(workspace_id)

    # ---------- sessions ----------

    def list_sessions(
        self,
        workspace_id: str,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        meta = self._require_workspace(workspace_id)
        persist = self._sessions_dir(meta["id"])
        items = Conversation.list_sessions(persist, limit=200)
        enriched: list[dict[str, Any]] = []
        for item in items:
            item = dict(item)
            item["workspace_id"] = meta["id"]
            title = ""
            starred = False
            archived = False
            try:
                raw = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
                md = raw.get("metadata") or {}
                title = str(md.get("title") or "")
                starred = bool(md.get("starred"))
                archived = bool(md.get("archived"))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                pass
            item["title"] = title or item.get("preview") or "新对话"
            item["starred"] = starred
            item["archived"] = archived
            if archived and not include_archived:
                continue
            enriched.append(item)
        enriched.sort(
            key=lambda x: (
                0 if x.get("starred") else 1,
                0 if not x.get("archived") else 1,
                -(self._parse_ts(x.get("updated_at"))),
            )
        )
        return enriched

    def create_session(
        self,
        workspace_id: str,
        *,
        title: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        meta = self._require_workspace(workspace_id)
        workdir = meta["path"]
        persist = self._sessions_dir(meta["id"])
        agent = self._make_agent(workdir, detail=detail)
        conv = Conversation(agent, persist_dir=persist)
        conv.metadata["workspace_id"] = meta["id"]
        conv.metadata["starred"] = False
        conv.metadata["archived"] = False
        if title:
            conv.metadata["title"] = title.strip()
        path = conv.save()
        self._wire_ask_user(conv)
        with self._global:
            self._convs[conv.session_id] = conv
            self._locks.setdefault(conv.session_id, threading.Lock())
        self._touch_workspace(meta["id"])
        logger.notice(f"会话已创建: {conv.session_id[:8]} @ {meta['id']}")
        return self._session_summary(conv, path)

    def get_session(self, session_id: str) -> dict[str, Any]:
        conv = self._get_or_load(session_id)
        path = conv.last_save_path or conv._default_persist_path()
        return self._session_detail(conv, path)

    def delete_session(self, session_id: str) -> None:
        conv = self._get_or_load(session_id)
        path = conv.last_save_path or conv._default_persist_path()
        wid = str(conv.metadata.get("workspace_id") or "")
        with self._global:
            self._convs.pop(session_id, None)
            self._locks.pop(session_id, None)
            pending = self._pending_asks.pop(session_id, None)
        if pending and pending.get("queue") is not None:
            try:
                pending["queue"].put_nowait("")
            except queue.Full:
                pass
        self._clear_ask_checkpoint(session_id)
        if path.is_file():
            path.unlink()
        if wid:
            self._touch_workspace(wid)
        logger.notice(f"会话已删除: {session_id[:8]}")

    def answer_ask(
        self,
        session_id: str,
        *,
        ask_id: str,
        raw: str = "",
        answers: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """回填 ask_user 选择。不得持有会话锁（运行线程可能已占用）。"""
        sid = session_id.strip()
        aid = (ask_id or "").strip()
        if not aid:
            raise ValueError("ask_id 不能为空")
        self._hydrate_pending_ask(sid)
        with self._global:
            pending = self._pending_asks.get(sid)
            if not pending or pending.get("ask_id") != aid:
                raise KeyError("没有匹配的待回答提问（可能已超时或已作答）")
            answer_q = pending.get("queue")
            orphaned = bool(pending.get("orphaned") or answer_q is None)
            checkpoint = pending.get("checkpoint")
            if orphaned:
                self._pending_asks.pop(sid, None)
        if answers is not None:
            payload = json.dumps(answers, ensure_ascii=False)
        else:
            payload = str(raw if raw is not None else "")

        if orphaned:
            if not isinstance(checkpoint, dict):
                checkpoint = self._load_ask_checkpoint(sid) or {}
            if not checkpoint.get("messages"):
                # 回滚内存 pending，便于重试
                with self._global:
                    self._pending_asks[sid] = {
                        "ask_id": aid,
                        "queue": None,
                        "event": checkpoint.get("event") or {"ask_id": aid},
                        "orphaned": True,
                        "checkpoint": checkpoint,
                    }
                raise RuntimeError("待确认检查点不完整，无法在重启后续跑")
            threading.Thread(
                target=self._resume_orphaned_ask,
                kwargs={
                    "session_id": sid,
                    "checkpoint": checkpoint,
                    "payload": payload,
                    "ask_id": aid,
                },
                name=f"web-resume-ask-{sid[:8]}",
                daemon=True,
            ).start()
            logger.notice(f"已提交孤儿 ask_user 回答并续跑: {sid[:8]} ask={aid}")
            return {"ok": True, "ask_id": aid, "resumed": True}

        try:
            answer_q.put_nowait(payload)
        except queue.Full as exc:
            raise RuntimeError("该提问已收到回答") from exc
        logger.notice(f"已提交 ask_user 回答: {sid[:8]} ask={aid}")
        return {"ok": True, "ask_id": aid, "resumed": False}

    def patch_session(
        self,
        session_id: str,
        *,
        mode: str | None = None,
        detail: str | None = None,
        title: str | None = None,
        starred: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        conv = self._get_or_load(session_id)
        with self._lock_for(session_id):
            if mode is not None:
                conv.set_mode(mode)
            if detail is not None:
                conv.set_detail(detail)
            if title is not None:
                conv.metadata["title"] = title.strip()
            if starred is not None:
                conv.metadata["starred"] = bool(starred)
            if archived is not None:
                conv.metadata["archived"] = bool(archived)
                if archived:
                    conv.metadata["archived_at"] = _now_iso()
                else:
                    conv.metadata.pop("archived_at", None)
            path = conv.save()
            wid = str(conv.metadata.get("workspace_id") or "")
            if wid and (starred is not None or archived is not None or title is not None):
                self._touch_workspace(wid)
        return self._session_detail(conv, path)

    def chat(self, session_id: str, message: str) -> dict[str, Any]:
        return self._run_turn(session_id, lambda conv: conv.chat(message))

    def confirm_plan(self, session_id: str) -> dict[str, Any]:
        return self._run_turn(session_id, lambda conv: conv.confirm_plan())

    def iter_chat_events(self, session_id: str, message: str) -> Iterator[dict[str, Any]]:
        """同步生成器：status/step/assistant_delta/skill/cancelled + 最终 done/error。"""
        yield from self._iter_run_events(
            session_id,
            started_message="开始处理…",
            runner=lambda conv: conv.chat(message),
        )

    def iter_confirm_events(self, session_id: str) -> Iterator[dict[str, Any]]:
        yield from self._iter_run_events(
            session_id,
            started_message="开始执行计划…",
            runner=lambda conv: conv.confirm_plan(),
        )

    def cancel_run(self, session_id: str) -> dict[str, Any]:
        """请求取消当前正在执行的 turn（不抢会话锁，避免与运行线程死锁）。"""
        with self._global:
            conv = self._convs.get(session_id.strip())
        if conv is None:
            # 未在跑则无需加载整会话
            return {"ok": False, "session_id": session_id, "cancelled": False}
        conv.cancel()
        return {"ok": True, "session_id": session_id, "cancelled": True}

    def enqueue_message(self, session_id: str, text: str) -> dict[str, Any]:
        """向当前 turn 队列中途补充用户消息。"""
        with self._global:
            conv = self._convs.get(session_id.strip())
        if conv is None:
            return {"ok": False, "session_id": session_id, "enqueued": False}
        conv.enqueue(text)
        return {"ok": True, "session_id": session_id, "enqueued": True}

    def _run_turn(self, session_id: str, runner) -> dict[str, Any]:
        conv = self._get_or_load(session_id)
        with self._lock_for(session_id):
            result = runner(conv)
            path = conv.last_save_path or conv.save()
            wid = str(conv.metadata.get("workspace_id") or "")
            if wid:
                self._touch_workspace(wid)
            detail = self._session_detail(conv, path)
            detail["answer"] = result.answer
            detail["completed"] = result.completed
            detail["detail_text"] = result.detail_text
            detail["detail_level"] = result.detail_level
            return detail

    def _iter_run_events(
        self,
        session_id: str,
        *,
        started_message: str,
        runner,
    ) -> Iterator[dict[str, Any]]:
        q: queue.Queue[tuple[str, Any]] = queue.Queue()
        # 合并流式 delta，避免把 Qt 事件队列打满导致界面假死、done 迟迟无法处理
        delta_lock = threading.Lock()
        delta_buf = {"text": "", "step": None, "last": 0.0}

        def _flush_delta(*, force: bool = False) -> None:
            with delta_lock:
                text = delta_buf["text"]
                if not text:
                    return
                now = time.monotonic()
                if not force and (now - float(delta_buf["last"])) < 0.12 and len(text) < 160:
                    return
                step = delta_buf["step"]
                delta_buf["text"] = ""
                delta_buf["last"] = now
            q.put(
                (
                    "progress",
                    {
                        "type": "assistant_delta",
                        "step": step,
                        "delta": text,
                    },
                )
            )

        def on_progress(event: dict[str, Any]) -> None:
            if isinstance(event, dict) and event.get("type") == "assistant_delta":
                with delta_lock:
                    delta_buf["text"] += str(event.get("delta") or "")
                    if event.get("step") is not None:
                        delta_buf["step"] = event.get("step")
                    # 过长直接截断缓冲，UI 本就不展示全文
                    if len(delta_buf["text"]) > 4000:
                        delta_buf["text"] = "…" + delta_buf["text"][-2000:]
                    ready = (time.monotonic() - float(delta_buf["last"])) >= 0.12 or len(
                        delta_buf["text"]
                    ) >= 160
                if ready:
                    _flush_delta(force=True)
                return
            _flush_delta(force=True)
            q.put(("progress", event))

        def worker() -> None:
            try:
                conv = self._get_or_load(session_id)
                with self._lock_for(session_id):
                    prev_progress = conv.agent.on_progress
                    conv.agent.on_progress = on_progress
                    try:
                        result = runner(conv)
                        path = conv.last_save_path or conv.save()
                        wid = str(conv.metadata.get("workspace_id") or "")
                        if wid:
                            self._touch_workspace(wid)
                        detail = self._session_detail(conv, path)
                        detail["answer"] = result.answer
                        detail["completed"] = result.completed
                        detail["detail_text"] = result.detail_text
                        detail["detail_level"] = result.detail_level
                        _flush_delta(force=True)
                        q.put(("done", detail))
                    finally:
                        conv.agent.on_progress = prev_progress
            except Exception as exc:  # noqa: BLE001
                q.put(("error", exc))
            finally:
                _flush_delta(force=True)
                q.put(("end", None))

        yield {
            "type": "status",
            "phase": "started",
            "message": started_message,
        }
        threading.Thread(target=worker, name=f"app-run-{session_id[:8]}", daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind == "progress":
                yield payload if isinstance(payload, dict) else {"type": "status", "message": str(payload)}
            elif kind == "done":
                yield {"type": "done", "session": payload}
            elif kind == "error":
                yield {
                    "type": "error",
                    "message": str(payload),
                }
            elif kind == "end":
                break

    # ---------- internals ----------

    def _make_agent(self, workdir: str, *, detail: str | None = None) -> ReActAgent:
        guard = PermissionGuard.from_config()
        skills = SkillRegistry.from_config(permission=guard, load_permission=False, workdir=workdir)
        return ReActAgent(
            skills=skills,
            permission=guard,
            mode=AgentMode.AGENT,
            workdir=workdir,
            detail=detail,
        )

    def _get_or_load(self, session_id: str) -> Conversation:
        with self._global:
            cached = self._convs.get(session_id)
            if cached is not None:
                return cached

        path = self._find_session_file(session_id)
        if path is None:
            raise FileNotFoundError(f"未找到会话: {session_id}")

        data = json.loads(path.read_text(encoding="utf-8"))
        workdir = str(data.get("workdir") or "").strip()
        wid = str((data.get("metadata") or {}).get("workspace_id") or "")
        if not workdir and wid:
            meta = self._read_meta(wid)
            if meta:
                workdir = meta["path"]
        if not workdir:
            raise RuntimeError(f"会话缺少 workdir: {session_id}")

        agent = self._make_agent(workdir)
        conv = Conversation.load(path, agent=agent, persist_dir=path.parent)
        if wid and not conv.metadata.get("workspace_id"):
            conv.metadata["workspace_id"] = wid
        self._wire_ask_user(conv)
        with self._global:
            self._convs[conv.session_id] = conv
            self._locks.setdefault(conv.session_id, threading.Lock())
        return conv

    def _wire_ask_user(self, conv: Conversation) -> None:
        """将 ask_user 接到工作台 SSE，避免阻塞在服务端 stdin。"""
        sid = conv.session_id

        def handler(question: str, options: list[str], meta: dict[str, Any]) -> str:
            return self._web_ask(sid, question, options, meta)

        conv.agent.skills.register(AskUserSkill(ask_handler=handler))

    def _web_ask(
        self,
        session_id: str,
        question: str,
        options: list[str],
        meta: dict[str, Any],
    ) -> str:
        with self._global:
            conv = self._convs.get(session_id)
        if conv is None:
            raise RuntimeError("会话未加载，无法向用户提问")
        progress = getattr(conv.agent, "on_progress", None)
        if progress is None:
            raise RuntimeError(
                "ask_user 需要流式运行（iter_chat_events / iter_confirm_events）以便作答"
            )

        ask_id = uuid.uuid4().hex[:12]
        questions = meta.get("questions")
        if not isinstance(questions, list) or not questions:
            questions = [
                {
                    "id": "1",
                    "question": question,
                    "options": list(options),
                    "allow_multiple": bool(meta.get("allow_multiple", False)),
                    "allow_custom": bool(meta.get("allow_custom", True)),
                    "default": meta.get("default"),
                    "context": str(meta.get("context") or ""),
                }
            ]
        event = {
            "type": "ask_user",
            "ask_id": ask_id,
            "question": question,
            "options": list(options),
            "questions": questions,
            "allow_multiple": bool(meta.get("allow_multiple", False)),
            "allow_custom": bool(meta.get("allow_custom", True)),
            "default": meta.get("default"),
            "context": str(meta.get("context") or ""),
        }
        live = getattr(conv.agent, "_live_messages", None) or []
        msg_payload = [
            {"role": m.role, "content": m.content, "name": m.name}
            for m in live
            if getattr(m, "role", None)
        ]
        checkpoint = {
            "ask_id": ask_id,
            "event": event,
            "mode": conv.agent.mode.value,
            "user_task": _extract_user_task(list(live)),
            "messages": msg_payload,
            "created_at": _now_iso(),
        }
        self._write_ask_checkpoint(session_id, checkpoint)

        answer_q: queue.Queue[str] = queue.Queue(maxsize=1)
        with self._global:
            old = self._pending_asks.get(session_id)
            self._pending_asks[session_id] = {
                "ask_id": ask_id,
                "queue": answer_q,
                "event": event,
                "orphaned": False,
                "checkpoint": checkpoint,
            }
        if old is not None and old.get("queue") is not None:
            try:
                old["queue"].put_nowait("")
            except queue.Full:
                pass

        try:
            progress({"type": "status", "phase": "ask_user", "message": "等待你的选择…"})
            progress(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"推送 ask_user 事件失败: {exc}")

        try:
            return str(answer_q.get(timeout=ASK_USER_TIMEOUT_SEC))
        except queue.Empty as exc:
            raise TimeoutError(
                f"等待用户选择超时（{int(ASK_USER_TIMEOUT_SEC)}s）"
            ) from exc
        finally:
            with self._global:
                pending = self._pending_asks.get(session_id)
                if pending and pending.get("ask_id") == ask_id:
                    self._pending_asks.pop(session_id, None)
            self._clear_ask_checkpoint(session_id)

    def _find_session_file(self, session_id: str) -> Path | None:
        sid = session_id.strip()
        for ws in self.list_workspaces():
            persist = self._sessions_dir(ws["id"])
            exact = persist / f"{sid}.json"
            if exact.is_file():
                return exact
            matches = list(persist.glob(f"{sid}*.json"))
            if len(matches) == 1:
                return matches[0]
        return None

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._global:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def _sessions_dir(self, workspace_id: str) -> Path:
        path = self.root / workspace_id / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _require_workspace(self, workspace_id: str) -> dict[str, Any]:
        meta = self._read_meta(workspace_id)
        if meta is None:
            raise FileNotFoundError(f"未找到工作区: {workspace_id}")
        index = self._read_index()
        if workspace_id not in (index.get("workspace_ids") or []):
            raise FileNotFoundError(f"工作区未注册: {workspace_id}")
        return meta

    def _touch_workspace(self, workspace_id: str) -> None:
        meta = self._read_meta(workspace_id)
        if meta is None:
            return
        meta["updated_at"] = _now_iso()
        self._write_meta(workspace_id, meta)

    def _workspace_public(self, meta: dict[str, Any]) -> dict[str, Any]:
        item = dict(meta)
        item["collapsed"] = bool(meta.get("collapsed", False))
        return item

    @staticmethod
    def _parse_ts(value: Any) -> float:
        if not value:
            return 0.0
        try:
            text = str(value).replace("Z", "+00:00")
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            return 0.0

    def _session_summary(self, conv: Conversation, path: Path) -> dict[str, Any]:
        preview = ""
        if conv.turns:
            preview = conv.turns[0].user.replace("\n", " ").strip()[:80]
        title = str(conv.metadata.get("title") or preview or "新对话")
        return {
            "session_id": conv.session_id,
            "workspace_id": conv.metadata.get("workspace_id"),
            "path": str(path),
            "turn_count": conv.turn_count,
            "preview": preview,
            "title": title,
            "starred": bool(conv.metadata.get("starred")),
            "archived": bool(conv.metadata.get("archived")),
            "workdir": str(conv.agent.workdir),
            "mode": conv.agent.mode.value,
            "detail": conv.agent.detail,
            "updated_at": _now_iso(),
        }

    def _session_detail(self, conv: Conversation, path: Path) -> dict[str, Any]:
        summary = self._session_summary(conv, path)
        summary["turns"] = [
            {
                "index": t.index,
                "user": t.user,
                "answer": t.answer,
                "completed": t.completed,
                "mode": t.mode,
                "step_count": t.step_count,
                "changes": list(t.changes or []),
                "ask_answers": list(getattr(t, "ask_answers", None) or []),
            }
            for t in conv.turns
        ]
        plan = conv.pending_plan
        if plan is not None:
            pdata = plan.to_dict()
            summary["pending_plan"] = {
                "ok": bool(pdata.get("ok")),
                "summary": pdata.get("summary") or "",
                "text": plan.format_text(),
                "steps": pdata.get("steps") or [],
            }
        else:
            summary["pending_plan"] = None
        summary["pending_ask"] = self.get_pending_ask(conv.session_id)
        summary["todos"] = self._load_todos(str(conv.agent.workdir))
        return summary

    def get_todos(self, session_id: str) -> dict[str, Any]:
        conv = self._get_or_load(session_id)
        return self._load_todos(str(conv.agent.workdir))

    @staticmethod
    def _load_todos(workdir: str) -> dict[str, Any]:
        from skills.todo_tracker import load_todos

        try:
            return load_todos(workdir)
        except Exception:  # noqa: BLE001
            return {"items": [], "updated_at": None, "counts": {}}

    def get_pending_ask(self, session_id: str) -> dict[str, Any] | None:
        """返回当前等待用户作答的 ask_user 事件（若有；含重启后孤儿检查点）。"""
        sid = session_id.strip()
        self._hydrate_pending_ask(sid)
        with self._global:
            pending = self._pending_asks.get(sid)
            if not pending:
                return None
            # 孤儿态若磁盘检查点已不在，清理幽灵内存项
            if pending.get("orphaned") and pending.get("queue") is None:
                ck = self._ask_checkpoint_path(sid)
                if ck is None or not ck.is_file():
                    self._pending_asks.pop(sid, None)
                    return None
            event = pending.get("event")
            return dict(event) if isinstance(event, dict) else None

    def _ask_checkpoint_path(self, session_id: str) -> Path | None:
        path = self._find_session_file(session_id)
        if path is None:
            return None
        return path.with_suffix(path.suffix + ".ask")

    def _write_ask_checkpoint(self, session_id: str, checkpoint: dict[str, Any]) -> None:
        target = self._ask_checkpoint_path(session_id)
        if target is None:
            logger.warning(f"无法落盘 ask 检查点（会话文件不存在）: {session_id[:8]}")
            return
        try:
            target.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.notice(f"ask 检查点已保存: {target.name}")
        except OSError as exc:
            logger.warning(f"写入 ask 检查点失败: {exc}")

    def _load_ask_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        target = self._ask_checkpoint_path(session_id)
        if target is None or not target.is_file():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取 ask 检查点失败: {exc}")
            return None
        if not isinstance(data, dict) or not data.get("ask_id") or not data.get("event"):
            return None
        return data

    def _clear_ask_checkpoint(self, session_id: str) -> None:
        target = self._ask_checkpoint_path(session_id)
        if target is None or not target.is_file():
            return
        try:
            target.unlink()
            logger.notice(f"ask 检查点已清除: {target.name}")
        except OSError as exc:
            logger.warning(f"清除 ask 检查点失败: {exc}")

    def _hydrate_pending_ask(self, session_id: str) -> None:
        """从磁盘恢复孤儿 ask（进程重启后内存队列已丢失）。"""
        sid = session_id.strip()
        with self._global:
            if sid in self._pending_asks:
                return
        checkpoint = self._load_ask_checkpoint(sid)
        if not checkpoint:
            return
        event = checkpoint.get("event")
        if not isinstance(event, dict):
            return
        with self._global:
            if sid in self._pending_asks:
                return
            self._pending_asks[sid] = {
                "ask_id": str(checkpoint.get("ask_id") or event.get("ask_id") or ""),
                "queue": None,
                "event": dict(event),
                "orphaned": True,
                "checkpoint": checkpoint,
            }
        logger.notice(f"已从磁盘恢复待确认 ask: {sid[:8]}")

    def _resume_orphaned_ask(
        self,
        *,
        session_id: str,
        checkpoint: dict[str, Any],
        payload: str,
        ask_id: str = "",
    ) -> None:
        try:
            conv = self._get_or_load(session_id)
            raw_messages = checkpoint.get("messages") or []
            messages = [
                AIMessage(
                    role=str(m.get("role") or "user"),  # type: ignore[arg-type]
                    content=str(m.get("content") or ""),
                    name=m.get("name"),
                )
                for m in raw_messages
                if isinstance(m, dict)
            ]
            mode = str(checkpoint.get("mode") or conv.agent.mode.value)
            user_task = str(checkpoint.get("user_task") or _extract_user_task(messages))
            # 先摘掉检查点，避免续跑期间 get_session 再次 hydrate 出幽灵 pending
            self._clear_ask_checkpoint(session_id)
            with self._global:
                self._pending_asks.pop(session_id, None)

            with self._lock_for(session_id):
                prev_progress = conv.agent.on_progress
                # 无 SSE 时也要有回调，否则续跑中再次 ask_user 会直接失败
                conv.agent.on_progress = prev_progress or (lambda _e: None)
                try:
                    result = conv.resume_after_ask(
                        messages=messages,
                        observation=payload,
                        mode=mode,
                        user_task=user_task,
                    )
                    path = conv.last_save_path or conv.save()
                    wid = str(conv.metadata.get("workspace_id") or "")
                    if wid:
                        self._touch_workspace(wid)
                    logger.notice(
                        f"孤儿 ask 续跑完成: {session_id[:8]} "
                        f"completed={result.completed} path={path.name} "
                        f"answer={(result.answer or '')[:80]}"
                    )
                finally:
                    conv.agent.on_progress = prev_progress
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"孤儿 ask 续跑失败: {session_id[:8]} {exc}")
            # 失败时恢复待确认，允许用户再答一次
            event = checkpoint.get("event") if isinstance(checkpoint.get("event"), dict) else None
            aid = ask_id or str(checkpoint.get("ask_id") or "")
            if event and aid:
                with self._global:
                    self._pending_asks[session_id] = {
                        "ask_id": aid,
                        "queue": None,
                        "event": dict(event),
                        "orphaned": True,
                        "checkpoint": checkpoint,
                    }
                self._write_ask_checkpoint(session_id, checkpoint)

    def _ensure_index(self) -> None:
        if not self._index_path.is_file():
            self._write_index({"workspace_ids": []})

    def _read_index(self) -> dict[str, Any]:
        if not self._index_path.is_file():
            return {"workspace_ids": []}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"workspace_ids": []}
        if not isinstance(data, dict):
            return {"workspace_ids": []}
        return data

    def _write_index(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_meta(self, workspace_id: str) -> dict[str, Any] | None:
        path = self.root / workspace_id / "meta.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write_meta(self, workspace_id: str, meta: dict[str, Any]) -> None:
        path = self.root / workspace_id / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
