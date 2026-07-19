"""子 Agent sidechain JSONL 落盘。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai import AIMessage


@dataclass
class SubagentMeta:
    agent_id: str
    parent_session_id: str = ""
    agent_type: str = "general"
    description: str = ""
    created_at: str = ""
    workdir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sidechain_dir(session_root: Path) -> Path:
    path = session_root / "subagents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def transcript_path(session_root: Path, agent_id: str) -> Path:
    return sidechain_dir(session_root) / f"agent-{agent_id}.jsonl"


def meta_path(session_root: Path, agent_id: str) -> Path:
    return sidechain_dir(session_root) / f"agent-{agent_id}.meta.json"


def write_meta(session_root: Path, meta: SubagentMeta) -> Path:
    if not meta.created_at:
        meta.created_at = datetime.now(timezone.utc).isoformat()
    path = meta_path(session_root, meta.agent_id)
    path.write_text(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_meta(session_root: Path, agent_id: str) -> SubagentMeta | None:
    path = meta_path(session_root, agent_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SubagentMeta(**{k: data.get(k, "") for k in SubagentMeta.__dataclass_fields__})


def append_message(session_root: Path, agent_id: str, message: AIMessage | dict[str, Any]) -> None:
    path = transcript_path(session_root, agent_id)
    if isinstance(message, AIMessage):
        row = {
            "role": message.role,
            "content": message.content,
            "name": message.name,
            "tool_call_id": message.tool_call_id,
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
                for tc in (message.tool_calls or [])
            ],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    else:
        row = dict(message)
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_messages(session_root: Path, agent_id: str) -> list[AIMessage]:
    from ai import ToolCall

    path = transcript_path(session_root, agent_id)
    if not path.is_file():
        return []
    out: list[AIMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        tcs = []
        for tc in row.get("tool_calls") or []:
            if isinstance(tc, dict):
                tcs.append(
                    ToolCall(
                        id=str(tc.get("id") or ""),
                        name=str(tc.get("name") or ""),
                        arguments=str(tc.get("arguments") or ""),
                    )
                )
        out.append(
            AIMessage(
                role=str(row.get("role") or "user"),
                content=str(row.get("content") or ""),
                name=row.get("name"),
                tool_call_id=row.get("tool_call_id"),
                tool_calls=tcs,
            )
        )
    return out
