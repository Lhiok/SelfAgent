"""本地文件查询 / 修改 Skill，辅助 AI 了解与修改项目。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.local_file")


class LocalFileSkill(Skill):
    name = "local_file"
    description = (
        "本地文件能力：list 列出目录、read 读取文件、write 写入/覆盖、"
        "patch 按旧文本替换、move 移动/重命名文件或目录。"
        "用于了解项目结构并修改代码。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "read", "write", "patch", "move"],
                "description": "操作类型",
            },
            "path": {"type": "string", "description": "相对 root 的路径（move 时为源路径）"},
            "dest": {
                "type": "string",
                "description": "move 时的目标路径（相对 root）；可为新文件名或目录",
            },
            "content": {"type": "string", "description": "write 时的完整内容"},
            "old_text": {"type": "string", "description": "patch 时要替换的原文"},
            "new_text": {"type": "string", "description": "patch 时的新文本"},
            "encoding": {"type": "string", "default": "utf-8"},
            "max_chars": {
                "type": "integer",
                "default": 20000,
                "description": "read 最大返回字符数",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "move 时若目标已存在是否覆盖（仅文件）",
            },
        },
        "required": ["action", "path"],
    }

    def __init__(self, root: str | Path = ".", *, allow_write: bool = True) -> None:
        self.root = Path(root).resolve()
        self.allow_write = allow_write

    def run(self, **kwargs: Any) -> SkillResult:
        action = str(kwargs.get("action", "")).strip().lower()
        rel = str(kwargs.get("path", "")).strip()
        encoding = str(kwargs.get("encoding") or "utf-8")
        max_chars = int(kwargs.get("max_chars") or 20000)

        if not action or not rel:
            return SkillResult(ok=False, output="缺少 action 或 path")

        try:
            target = self._safe_path(rel)
        except ValueError as exc:
            logger.warning(str(exc))
            return SkillResult(ok=False, output=str(exc))

        handlers = {
            "list": lambda: self._list(target),
            "read": lambda: self._read(target, encoding, max_chars),
            "write": lambda: self._write(target, kwargs.get("content"), encoding),
            "patch": lambda: self._patch(
                target,
                kwargs.get("old_text"),
                kwargs.get("new_text"),
                encoding,
            ),
            "move": lambda: self._move(
                target,
                kwargs.get("dest"),
                overwrite=bool(kwargs.get("overwrite", False)),
            ),
        }
        handler = handlers.get(action)
        if handler is None:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")
        return handler()

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径越界，禁止访问 root 之外: {rel}") from exc
        return candidate

    def _list(self, target: Path) -> SkillResult:
        if not target.exists():
            return SkillResult(ok=False, output=f"路径不存在: {target}")
        if target.is_file():
            return SkillResult(
                ok=True,
                output=json.dumps(
                    {
                        "type": "file",
                        "path": str(target.relative_to(self.root)).replace("\\", "/"),
                        "size": target.stat().st_size,
                    },
                    ensure_ascii=False,
                ),
            )

        entries = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            rel = str(child.relative_to(self.root)).replace("\\", "/")
            entries.append(
                {
                    "name": child.name,
                    "path": rel,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return SkillResult(
            ok=True,
            output=json.dumps(entries, ensure_ascii=False, indent=2),
            data={"count": len(entries)},
        )

    def _read(self, target: Path, encoding: str, max_chars: int) -> SkillResult:
        if not target.is_file():
            return SkillResult(ok=False, output=f"不是文件或不存在: {target}")
        text = target.read_text(encoding=encoding)
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        note = "\n\n...[truncated]..." if truncated else ""
        return SkillResult(
            ok=True,
            output=text + note,
            data={
                "truncated": truncated,
                "path": str(target.relative_to(self.root)).replace("\\", "/"),
            },
        )

    def _write(self, target: Path, content: Any, encoding: str) -> SkillResult:
        if not self.allow_write:
            return SkillResult(ok=False, output="当前配置禁止写入")
        if content is None:
            return SkillResult(ok=False, output="write 需要 content")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding=encoding)
        logger.notice(f"已写入文件: {target}")
        return SkillResult(
            ok=True,
            output=(
                f"已写入 {str(target.relative_to(self.root)).replace(chr(92), '/')}"
                f"，字节数约 {len(str(content).encode(encoding))}"
            ),
        )

    def _patch(self, target: Path, old_text: Any, new_text: Any, encoding: str) -> SkillResult:
        if not self.allow_write:
            return SkillResult(ok=False, output="当前配置禁止写入")
        if old_text is None or new_text is None:
            return SkillResult(ok=False, output="patch 需要 old_text 与 new_text")
        if not target.is_file():
            return SkillResult(ok=False, output=f"不是文件或不存在: {target}")

        original = target.read_text(encoding=encoding)
        old = str(old_text)
        new = str(new_text)
        count = original.count(old)
        if count == 0:
            return SkillResult(ok=False, output="未找到匹配的 old_text")
        if count > 1:
            return SkillResult(
                ok=False,
                output=f"old_text 匹配到 {count} 处，请提供更唯一的片段",
            )
        target.write_text(original.replace(old, new, 1), encoding=encoding)
        logger.notice(f"已 patch 文件: {target}")
        rel = str(target.relative_to(self.root)).replace("\\", "/")
        return SkillResult(ok=True, output=f"已更新 {rel}")

    def _move(self, source: Path, dest: Any, *, overwrite: bool = False) -> SkillResult:
        if not self.allow_write:
            return SkillResult(ok=False, output="当前配置禁止写入")
        if dest is None or str(dest).strip() == "":
            return SkillResult(ok=False, output="move 需要 dest（目标路径）")
        if not source.exists():
            return SkillResult(ok=False, output=f"源路径不存在: {source}")

        try:
            destination = self._safe_path(str(dest).strip())
        except ValueError as exc:
            logger.warning(str(exc))
            return SkillResult(ok=False, output=str(exc))

        if destination.exists() and destination.is_dir():
            destination = destination / source.name

        if destination.exists():
            if destination.is_dir():
                return SkillResult(ok=False, output=f"目标已存在且为目录: {destination}")
            if not overwrite:
                return SkillResult(
                    ok=False,
                    output=f"目标已存在: {destination}（可传 overwrite=true 覆盖）",
                )
            destination.unlink()

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except OSError as exc:
            logger.critical(f"移动失败: {exc}")
            return SkillResult(ok=False, output=f"移动失败: {exc}")

        src_rel = str(source.relative_to(self.root)).replace("\\", "/")
        dst_rel = str(destination.relative_to(self.root)).replace("\\", "/")
        logger.notice(f"已移动: {src_rel} -> {dst_rel}")
        return SkillResult(
            ok=True,
            output=f"已移动 {src_rel} -> {dst_rel}",
            data={"from": src_rel, "to": dst_rel},
        )
