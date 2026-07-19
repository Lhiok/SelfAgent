"""FastAPI 应用：工作台 API + 静态 SPA。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from web.store import WorkspaceStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


class WorkspaceCreate(BaseModel):
    path: str
    title: str | None = None


class WorkspacePatch(BaseModel):
    title: str | None = None
    collapsed: bool | None = None


class SessionCreate(BaseModel):
    title: str | None = None
    detail: str | None = None


class SessionPatch(BaseModel):
    mode: str | None = None
    detail: str | None = None
    title: str | None = None
    starred: bool | None = None
    archived: bool | None = None


class ChatBody(BaseModel):
    message: str = Field(min_length=1)


class AskAnswerBody(BaseModel):
    ask_id: str = Field(min_length=1)
    raw: str = ""
    answers: list[dict[str, Any]] | None = None


class PermissionAnswerBody(BaseModel):
    ask_id: str = Field(min_length=1)
    allow: bool = True


class WorkflowRunBody(BaseModel):
    name: str = Field(min_length=1)
    session_id: str | None = None


class PlanRejectBody(BaseModel):
    feedback: str = ""


def create_app(store: WorkspaceStore | None = None) -> FastAPI:
    app = FastAPI(title="SelfAgent Workbench", version="0.1.0")
    app.state.store = store or WorkspaceStore()

    def store_of() -> WorkspaceStore:
        return app.state.store

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/mcp")
    def list_mcp() -> dict[str, Any]:
        """列出已配置/已连接的 MCP 工具。"""
        try:
            from mcp import McpManager, load_mcp_servers

            servers = [
                {
                    "name": s.name,
                    "transport": s.transport,
                    "enabled": s.enabled,
                    "command": s.command,
                }
                for s in load_mcp_servers()
            ]
            mgr = getattr(app.state, "mcp_manager", None)
            tools = []
            if mgr is not None:
                tools = [
                    {
                        "name": t.tool_name,
                        "server": t.server,
                        "description": t.description,
                    }
                    for t in mgr.list_tools()
                ]
            return {"servers": servers, "tools": tools}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/mcp/reload")
    def reload_mcp() -> dict[str, Any]:
        try:
            from mcp import McpManager

            old = getattr(app.state, "mcp_manager", None)
            if old is not None:
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
            mgr = McpManager.from_config()
            app.state.mcp_manager = mgr
            return {
                "ok": True,
                "tools": [
                    {"name": t.tool_name, "server": t.server}
                    for t in mgr.list_tools()
                ],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/workspaces")
    def list_workspaces() -> list[dict[str, Any]]:
        return store_of().list_workspaces()

    @app.post("/api/workspaces")
    def add_workspace(body: WorkspaceCreate) -> dict[str, Any]:
        try:
            return store_of().add_workspace(body.path, title=body.title)
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str) -> dict[str, Any]:
        try:
            return store_of().get_workspace(workspace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/workspaces/{workspace_id}")
    def patch_workspace(workspace_id: str, body: WorkspacePatch) -> dict[str, Any]:
        try:
            return store_of().patch_workspace(
                workspace_id, title=body.title, collapsed=body.collapsed
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/workspaces/{workspace_id}")
    def delete_workspace(workspace_id: str) -> dict[str, bool]:
        try:
            store_of().remove_workspace(workspace_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/api/workspaces/{workspace_id}/sessions")
    def list_sessions(
        workspace_id: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        try:
            return store_of().list_sessions(
                workspace_id, include_archived=include_archived
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/workspaces/{workspace_id}/sessions")
    def create_session(workspace_id: str, body: SessionCreate | None = None) -> dict[str, Any]:
        body = body or SessionCreate()
        try:
            return store_of().create_session(
                workspace_id, title=body.title, detail=body.detail
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            return store_of().get_session(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}/todos")
    def get_todos(session_id: str) -> dict[str, Any]:
        try:
            return store_of().get_todos(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/sessions/{session_id}/memory")
    def get_memory(session_id: str) -> dict[str, Any]:
        try:
            return store_of().get_session_memory(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/sessions/{session_id}")
    def patch_session(session_id: str, body: SessionPatch) -> dict[str, Any]:
        try:
            return store_of().patch_session(
                session_id,
                mode=body.mode,
                detail=body.detail,
                title=body.title,
                starred=body.starred,
                archived=body.archived,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, bool]:
        try:
            store_of().delete_session(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    def _sse_bytes(event: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")

    def _stream_events(events: Iterator[dict[str, Any]]) -> Iterator[bytes]:
        try:
            for event in events:
                yield _sse_bytes(event)
        except FileNotFoundError as exc:
            yield _sse_bytes({"type": "error", "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            yield _sse_bytes({"type": "error", "message": str(exc)})

    @app.post("/api/sessions/{session_id}/answer")
    def answer_ask(session_id: str, body: AskAnswerBody) -> dict[str, Any]:
        try:
            return store_of().answer_ask(
                session_id,
                ask_id=body.ask_id,
                raw=body.raw,
                answers=body.answers,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/permission")
    def answer_permission(session_id: str, body: PermissionAnswerBody) -> dict[str, Any]:
        try:
            return store_of().answer_permission(
                session_id,
                ask_id=body.ask_id,
                allow=body.allow,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatBody) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(store_of().chat, session_id, body.message)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/cancel")
    def cancel_run(session_id: str) -> dict[str, Any]:
        try:
            return store_of().cancel_run(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/chat/stream")
    async def chat_stream(session_id: str, body: ChatBody) -> StreamingResponse:
        store = store_of()

        def generate() -> Iterator[bytes]:
            yield from _stream_events(store.iter_chat_events(session_id, body.message))

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/sessions/{session_id}/confirm")
    async def confirm(session_id: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(store_of().confirm_plan, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/reject")
    async def reject_plan(session_id: str, body: PlanRejectBody | None = None) -> dict[str, Any]:
        fb = (body.feedback if body is not None else "") or ""
        try:
            return await asyncio.to_thread(store_of().reject_plan, session_id, fb)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/workflow/run")
    async def workflow_run(body: WorkflowRunBody) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                store_of().run_workflow,
                body.name,
                session_id=body.session_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/workflow/{run_id}")
    def workflow_get(run_id: str) -> dict[str, Any]:
        try:
            return store_of().get_workflow_run(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks")
    def tasks_list(list_id: str = "default") -> dict[str, Any]:
        return store_of().list_workflow_tasks(list_id)

    @app.post("/api/sessions/{session_id}/confirm/stream")
    async def confirm_stream(session_id: str) -> StreamingResponse:
        store = store_of()

        def generate() -> Iterator[bytes]:
            yield from _stream_events(store.iter_confirm_events(session_id))

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
