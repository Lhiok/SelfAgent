"""在项目内搜索代码（关键词 / 正则）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.search_code")

_DEFAULT_SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
}


class SearchCodeSkill(Skill):
    name = "search_code"
    description = (
        "在项目内搜索代码：支持关键词或正则，可限制路径/扩展名。"
        "用于快速定位实现，优于反复 list+read。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search"],
                "description": "操作类型，目前仅 search",
                "default": "search",
            },
            "query": {"type": "string", "description": "搜索关键词或正则"},
            "path": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的搜索起点",
            },
            "regex": {
                "type": "boolean",
                "default": False,
                "description": "是否将 query 视为正则",
            },
            "case_sensitive": {"type": "boolean", "default": False},
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "仅搜索这些扩展名，如 [py, md, yaml]",
            },
            "max_results": {"type": "integer", "default": 50},
            "max_file_bytes": {"type": "integer", "default": 1048576},
            "context": {
                "type": "integer",
                "default": 0,
                "description": "匹配行上下各保留几行上下文",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        skip_dirs: set[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.skip_dirs = set(skip_dirs or _DEFAULT_SKIP_DIRS)

    def run(self, **kwargs: Any) -> SkillResult:
        action = str(kwargs.get("action") or "search").strip().lower()
        if action != "search":
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return SkillResult(ok=False, output="search_code 需要 query")

        rel = str(kwargs.get("path") or ".").strip() or "."
        use_regex = bool(kwargs.get("regex", False))
        case_sensitive = bool(kwargs.get("case_sensitive", False))
        max_results = int(kwargs.get("max_results") or 50)
        max_file_bytes = int(kwargs.get("max_file_bytes") or 1_048_576)
        context = max(0, int(kwargs.get("context") or 0))
        extensions = _normalize_exts(kwargs.get("extensions"))

        try:
            start = self._safe_path(rel)
        except ValueError as exc:
            return SkillResult(ok=False, output=str(exc))
        if not start.exists():
            return SkillResult(ok=False, output=f"路径不存在: {rel}")

        try:
            pattern = (
                re.compile(query if case_sensitive else query, 0 if case_sensitive else re.I)
                if use_regex
                else None
            )
        except re.error as exc:
            return SkillResult(ok=False, output=f"正则无效: {exc}")

        hits: list[dict[str, Any]] = []
        scanned = 0
        for file_path in self._iter_files(start, extensions):
            scanned += 1
            try:
                if file_path.stat().st_size > max_file_bytes:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            lines = text.splitlines()
            file_rel = str(file_path.relative_to(self.root)).replace("\\", "/")
            for i, line in enumerate(lines, start=1):
                matched = (
                    bool(pattern.search(line))
                    if pattern is not None
                    else (query in line if case_sensitive else query.lower() in line.lower())
                )
                if not matched:
                    continue
                snippet_lines = lines[max(0, i - 1 - context) : i + context]
                hits.append(
                    {
                        "path": file_rel,
                        "line": i,
                        "text": line[:500],
                        "snippet": "\n".join(snippet_lines)[:2000],
                    }
                )
                if len(hits) >= max_results:
                    break
            if len(hits) >= max_results:
                break

        payload = {
            "query": query,
            "regex": use_regex,
            "scanned_files": scanned,
            "count": len(hits),
            "truncated": len(hits) >= max_results,
            "hits": hits,
        }
        logger.notice(f"search_code query={query!r} hits={len(hits)} scanned={scanned}")
        return SkillResult(
            ok=True,
            output=json.dumps(payload, ensure_ascii=False, indent=2),
            data=payload,
        )

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径越界，禁止访问 root 之外: {rel}") from exc
        return candidate

    def _iter_files(self, start: Path, extensions: set[str] | None):
        if start.is_file():
            if extensions is None or start.suffix.lstrip(".").lower() in extensions:
                yield start
            return

        for path in start.rglob("*"):
            if not path.is_file():
                continue
            if any(part in self.skip_dirs for part in path.parts):
                continue
            if extensions is not None and path.suffix.lstrip(".").lower() not in extensions:
                continue
            yield path


def _normalize_exts(value: Any) -> set[str] | None:
    if not value:
        return None
    if not isinstance(value, list):
        return None
    out = {str(x).lstrip(".").lower() for x in value if str(x).strip()}
    return out or None
