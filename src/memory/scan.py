"""Topic 相关性扫描（关键词打分，无旁路模型）。"""

from __future__ import annotations

import re
from typing import Iterable

from memory.store import MemoryStore
from memory.types import MemoryEntry


def tokenize(text: str) -> set[str]:
    parts = re.findall(r"[\w\u4e00-\u9fff]{2,}", (text or "").lower())
    return set(parts)


def score_entry(entry: MemoryEntry, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    hay = " ".join(
        [
            entry.meta.name,
            entry.meta.description,
            entry.meta.type.value,
            entry.body[:2000],
        ]
    ).lower()
    tokens = tokenize(hay)
    if not tokens:
        return 0.0
    hit = len(query_tokens & tokens)
    # 名称/描述命中加权
    name_hit = len(query_tokens & tokenize(entry.meta.name + " " + entry.meta.description))
    return float(hit) + 2.0 * float(name_hit)


def find_relevant(
    store: MemoryStore,
    query: str,
    *,
    top_k: int = 5,
) -> list[MemoryEntry]:
    k = max(0, int(top_k))
    if k == 0:
        return []
    q_tokens = tokenize(query)
    scored: list[MemoryEntry] = []
    for entry in store.list_topics():
        s = score_entry(entry, q_tokens)
        if s <= 0 and q_tokens:
            continue
        entry.score = s if q_tokens else 0.1
        scored.append(entry)
    if not q_tokens:
        # 无 query 时返回最近修改的若干 topic（按文件名排序截断）
        return scored[:k]
    scored.sort(key=lambda e: e.score, reverse=True)
    return scored[:k]


def filter_by_names(entries: Iterable[MemoryEntry], names: set[str]) -> list[MemoryEntry]:
    wanted = {n.lower() for n in names}
    return [e for e in entries if e.meta.name.lower() in wanted]
