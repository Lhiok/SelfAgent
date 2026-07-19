"""记忆相关运行时设置读取。"""

from __future__ import annotations

from typing import Any


def compaction_settings(conv_cfg: dict[str, Any] | None) -> tuple[int, int, bool]:
    """返回 (compact_after, keep_recent, use_ai)。"""
    try:
        import config as cfg
        from memory.types import MemoryConfig

        mem = MemoryConfig.from_dict(cfg.get_section("memory", {}) or {})
        c = conv_cfg or {}
        after = int(c.get("compact_after_messages", mem.compact_after_messages) or 0)
        if "compact_after_messages" not in (conv_cfg or {}):
            after = mem.compact_after_messages
        keep = int(c.get("compact_keep_recent", mem.compact_keep_recent) or 12)
        use_ai = bool(c.get("compact_use_ai", False))
        return after, keep, use_ai
    except Exception:  # noqa: BLE001
        cfg = conv_cfg or {}
        after = int(cfg.get("compact_after_messages", 0) or 0)
        keep = int(cfg.get("compact_keep_recent", 12) or 12)
        use_ai = bool(cfg.get("compact_use_ai", False))
        return after, keep, use_ai
