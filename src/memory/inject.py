"""记忆注入：系统行为段 + 相关内容上下文。"""

from __future__ import annotations

from memory.scan import find_relevant
from memory.session import SessionMemory
from memory.store import MemoryStore
from memory.types import MemoryConfig

_MEMORY_BEHAVIOR = """## 持久记忆（memory）
你可读写工作目录下的 `.selfagent/memory/`（经 memory_ops skill）。
- MEMORY.md 仅为索引（短指针）；细节写在独立 topic `.md`（frontmatter: name/description/type）。
- type：user（用户偏好）、feedback（纠错/风格）、project（项目约定）、reference（稳定参考）。
- 不要保存：可随时 git/grep 得到的代码事实、密钥、一次性调试输出。
- 读取到的记忆可能过时；关键操作前用工具核实。
"""


def build_memory_system_section(*, enabled: bool = True) -> str:
    if not enabled:
        return ""
    return _MEMORY_BEHAVIOR.strip()


def build_memory_context(
    store: MemoryStore | None,
    query: str,
    *,
    config: MemoryConfig | None = None,
    session: SessionMemory | None = None,
    include_index: bool = True,
    include_session_notes: bool = False,
) -> str:
    """拼装注入到本轮的记忆正文（非 system 行为段）。"""
    cfg = config or MemoryConfig()
    if store is None or not cfg.enabled:
        return ""
    parts: list[str] = ["## 相关记忆"]
    if include_index:
        idx = (store.read_index() or "").strip()
        if idx:
            # 索引截断
            if len(idx) > 4000:
                idx = idx[:3999] + "…"
            parts.append("### 索引 (MEMORY.md)\n" + idx)
    topics = find_relevant(store, query, top_k=cfg.prefetch_top_k)
    if topics:
        parts.append("### 相关条目")
        for ent in topics:
            body = ent.body.strip()
            if len(body) > 1500:
                body = body[:1499] + "…"
            parts.append(
                f"#### {ent.meta.name} ({ent.meta.type.value})\n"
                f"{ent.meta.description}\n\n{body}"
            )
    if include_session_notes and session is not None:
        notes = (session.read() or "").strip()
        if notes:
            if len(notes) > 2000:
                notes = notes[:1999] + "…"
            parts.append("### 本会话笔记\n" + notes)
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)
