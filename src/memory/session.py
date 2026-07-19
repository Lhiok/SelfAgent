"""会话级 notes（compaction 底物）。"""

from __future__ import annotations

from pathlib import Path

from log import get_logger
from memory.paths import session_memory_dir

logger = get_logger("memory.session")

_TEMPLATE = """# Session Memory

## Current State
{state}

## Task
{task}

## Files / Paths
{files}

## Decisions / Constraints
{decisions}

## Errors
{errors}

## Open Items
{open_items}
"""


class SessionMemory:
    def __init__(self, persist_dir: str | Path, session_id: str) -> None:
        self.dir = session_memory_dir(persist_dir, session_id)
        self.path = self.dir / "summary.md"
        self.last_summarized_id: str = ""

    def ensure(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            self.path.write_text(
                _TEMPLATE.format(
                    state="(empty)",
                    task="(none)",
                    files="(none)",
                    decisions="(none)",
                    errors="(none)",
                    open_items="(none)",
                ),
                encoding="utf-8",
            )
        return self.path

    def read(self) -> str:
        self.ensure()
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write(self, text: str) -> None:
        self.ensure()
        self.path.write_text((text or "").rstrip() + "\n", encoding="utf-8")

    def update_from_turn(
        self,
        *,
        user: str,
        answer: str,
        message_id: str = "",
    ) -> None:
        """轻量追加本轮要点（不调用模型）。"""
        notes = self.read()
        u = (user or "").strip().replace("\n", " ")[:240]
        a = (answer or "").strip().replace("\n", " ")[:400]
        block = f"\n### Turn\n- user: {u}\n- answer: {a}\n"
        # 保持文件不至于无限膨胀
        if len(notes) > 12000:
            notes = notes[-8000:]
            notes = "# Session Memory (truncated)\n" + notes
        # 更新 Current State 段的粗略内容
        if "## Current State" in notes:
            parts = notes.split("## Current State", 1)
            rest = parts[1]
            if "## " in rest:
                _, after = rest.split("## ", 1)
                notes = (
                    parts[0]
                    + f"## Current State\n{a or u or '(updated)'}\n\n## "
                    + after
                )
            else:
                notes = parts[0] + f"## Current State\n{a or u}\n"
        notes = notes.rstrip() + block
        self.write(notes)
        if message_id:
            self.last_summarized_id = message_id
        logger.notice("session memory updated")
