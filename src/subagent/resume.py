"""续跑 sidechain。"""

from __future__ import annotations

from pathlib import Path

from ai import AIMessage
from subagent.transcript import load_messages, load_meta


def load_sidechain(
    session_root: Path,
    agent_id: str,
) -> tuple[list[AIMessage], dict]:
    meta = load_meta(session_root, agent_id)
    messages = load_messages(session_root, agent_id)
    meta_dict = meta.to_dict() if meta else {"agent_id": agent_id}
    return messages, meta_dict
