"""回合末轻量记忆抽取 + 写互斥。"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field

from ai import AIMessage
from log import get_logger
from memory.store import MemoryStore
from memory.types import MemoryConfig, MemoryType

logger = get_logger("memory.extract")


@dataclass
class MemoryWriteGuard:
    """本回合是否已写入 memdir（主 agent skill 写后跳过 extract）。"""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _written: bool = False

    def mark_written(self) -> None:
        with self._lock:
            self._written = True

    def clear(self) -> None:
        with self._lock:
            self._written = False

    @property
    def has_writes(self) -> bool:
        with self._lock:
            return self._written


def extract_from_turn(
    store: MemoryStore,
    *,
    user: str,
    answer: str,
    messages: list[AIMessage] | None = None,
    config: MemoryConfig | None = None,
    guard: MemoryWriteGuard | None = None,
) -> list[str]:
    """
    规则/启发式抽取（不强制调用模型，避免挡 UI）。
    返回写入的 topic 名列表。
    """
    cfg = config or store.config
    if not cfg.enabled or not cfg.extract_enabled:
        return []
    if guard is not None and guard.has_writes:
        logger.notice("extract skipped: memdir already written this turn")
        return []

    user_text = (user or "").strip()
    if len(user_text) < cfg.extract_min_user_chars:
        return []

    candidates = _heuristic_candidates(user_text, answer or "", messages or [])
    written: list[str] = []
    for name, body, mtype, desc in candidates:
        try:
            store.save_topic(name, body, description=desc, mem_type=mtype)
            written.append(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"extract save failed {name}: {exc}")
    if written and guard is not None:
        guard.mark_written()
    return written


def _heuristic_candidates(
    user: str,
    answer: str,
    messages: list[AIMessage],
) -> list[tuple[str, str, MemoryType, str]]:
    """从用户纠正/偏好句式提取少量 durable notes。"""
    out: list[tuple[str, str, MemoryType, str]] = []
    patterns = [
        (
            MemoryType.FEEDBACK,
            re.compile(
                r"(?:不要|别再|请改成|以后请|记住[:：]?)\s*(.+)$",
                re.MULTILINE,
            ),
        ),
        (
            MemoryType.USER,
            re.compile(
                r"(?:我喜欢|我偏好|请用|始终使用|默认用)\s*(.+)$",
                re.MULTILINE,
            ),
        ),
        (
            MemoryType.PROJECT,
            re.compile(
                r"(?:约定|规范|必须|项目里)\s*(.+)$",
                re.MULTILINE,
            ),
        ),
    ]
    for mtype, pat in patterns:
        m = pat.search(user)
        if not m:
            continue
        snippet = m.group(0).strip()
        if len(snippet) < 8:
            continue
        name = f"auto-{mtype.value}-{uuid.uuid4().hex[:8]}"
        body = f"来源用户：{snippet}\n\n助手回复摘要：{(answer or '')[:500]}"
        out.append((name, body, mtype, snippet[:120]))
        break  # 每回合最多一条启发式

    # 若用户明确说「记住」且尚无命中
    if not out and re.search(r"记住|记一下|save\s+to\s+memory", user, re.I):
        name = f"auto-note-{uuid.uuid4().hex[:8]}"
        body = f"用户请求记住：\n{user}\n\n上下文摘要：\n{(answer or '')[:800]}"
        out.append((name, body, MemoryType.REFERENCE, user[:120]))
    return out
