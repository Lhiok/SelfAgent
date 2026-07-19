"""Git 操作 Skill：默认只读，写操作需显式开启。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.git_ops")

_READ_ACTIONS = {"status", "diff", "log", "branch", "show"}
_WRITE_ACTIONS = {"add", "commit"}


class GitOpsSkill(Skill):
    name = "git_ops"
    description = (
        "Git 仓库操作：status/diff/log/branch/show 只读；"
        "add/commit 写入（需 allow_write）。不提供 push/pull/force。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "diff", "log", "branch", "show", "add", "commit"],
                "description": "操作类型",
            },
            "path": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的仓库路径（或仓库内子路径）",
            },
            "staged": {
                "type": "boolean",
                "default": False,
                "description": "diff 时是否只看已暂存变更",
            },
            "ref": {
                "type": "string",
                "description": "show 的对象（commit/sha）；diff 的对比引用可选",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "add / diff 限定的文件路径列表",
            },
            "message": {
                "type": "string",
                "description": "commit 提交说明",
            },
            "max_count": {
                "type": "integer",
                "default": 20,
                "description": "log 条数",
            },
            "all": {
                "type": "boolean",
                "default": False,
                "description": "branch 时是否列出全部本地分支",
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        allow_write: bool = False,
        git_bin: str = "git",
        timeout: float = 60.0,
        max_output_chars: int = 30000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.allow_write = allow_write
        self.git_bin = git_bin
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="git_ops 已在配置中禁用")

        action = str(kwargs.get("action") or "").strip().lower()
        if action not in _READ_ACTIONS | _WRITE_ACTIONS:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        if action in _WRITE_ACTIONS and not self.allow_write:
            return SkillResult(ok=False, output="当前配置禁止 git 写入（allow_write=false）")

        rel = str(kwargs.get("path") or ".").strip() or "."
        try:
            cwd = self._safe_path(rel)
        except ValueError as exc:
            return SkillResult(ok=False, output=str(exc))

        repo = self._find_repo(cwd)
        if repo is None:
            return SkillResult(ok=False, output=f"未找到 git 仓库: {rel}")

        handlers = {
            "status": lambda: self._status(repo),
            "diff": lambda: self._diff(repo, kwargs),
            "log": lambda: self._log(repo, kwargs),
            "branch": lambda: self._branch(repo, kwargs),
            "show": lambda: self._show(repo, kwargs),
            "add": lambda: self._add(repo, kwargs),
            "commit": lambda: self._commit(repo, kwargs),
        }
        return handlers[action]()

    def _status(self, repo: Path) -> SkillResult:
        return self._run_git(repo, ["status", "--short", "--branch"])

    def _diff(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        args = ["diff"]
        if bool(kwargs.get("staged", False)):
            args.append("--staged")
        ref = str(kwargs.get("ref") or "").strip()
        if ref:
            if not _safe_ref(ref):
                return SkillResult(ok=False, output=f"非法 ref: {ref}")
            args.append(ref)
        paths = _as_str_list(kwargs.get("paths"))
        if paths:
            try:
                rels = self._rel_paths(repo, paths)
            except ValueError as exc:
                return SkillResult(ok=False, output=str(exc))
            args.append("--")
            args.extend(rels)
        return self._run_git(repo, args)

    def _log(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        n = max(1, int(kwargs.get("max_count") or 20))
        return self._run_git(
            repo,
            ["log", f"-{n}", "--oneline", "--decorate", "--no-color"],
        )

    def _branch(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        args = ["branch", "--show-current"] if not bool(kwargs.get("all", False)) else ["branch", "--list"]
        return self._run_git(repo, args)

    def _show(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        ref = str(kwargs.get("ref") or "HEAD").strip() or "HEAD"
        if not _safe_ref(ref):
            return SkillResult(ok=False, output=f"非法 ref: {ref}")
        return self._run_git(repo, ["show", "--stat", "--no-color", ref])

    def _add(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        paths = _as_str_list(kwargs.get("paths"))
        if not paths:
            return SkillResult(ok=False, output="add 需要 paths（文件/目录列表）")
        try:
            rels = self._rel_paths(repo, paths)
        except ValueError as exc:
            return SkillResult(ok=False, output=str(exc))
        return self._run_git(repo, ["add", "--", *rels])

    def _commit(self, repo: Path, kwargs: dict[str, Any]) -> SkillResult:
        message = str(kwargs.get("message") or "").strip()
        if not message:
            return SkillResult(ok=False, output="commit 需要 message")
        # 避免 option injection
        if message.startswith("-"):
            return SkillResult(ok=False, output="commit message 不能以 - 开头")
        return self._run_git(repo, ["commit", "-m", message])

    def _run_git(self, repo: Path, args: list[str]) -> SkillResult:
        cmd = [self.git_bin, *args]
        logger.notice(f"git_ops: {' '.join(cmd)} (cwd={repo})")
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return SkillResult(ok=False, output="未找到 git 可执行文件，请确认已安装并在 PATH 中")
        except subprocess.TimeoutExpired:
            return SkillResult(ok=False, output=f"git 命令超时（>{self.timeout}s）")
        except OSError as exc:
            return SkillResult(ok=False, output=f"git 执行失败: {exc}")

        stdout = _clip(completed.stdout or "", self.max_output_chars)
        stderr = _clip(completed.stderr or "", self.max_output_chars)
        payload = {
            "argv": cmd,
            "cwd": str(repo),
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if completed.returncode != 0:
            return SkillResult(ok=False, output=text, data=payload)
        return SkillResult(ok=True, output=text, data=payload)

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径越界，禁止访问 root 之外: {rel}") from exc
        return candidate

    def _find_repo(self, start: Path) -> Path | None:
        cur = start if start.is_dir() else start.parent
        root = self.root
        while True:
            if (cur / ".git").exists():
                return cur
            if cur == root or cur.parent == cur:
                break
            try:
                cur.relative_to(root)
            except ValueError:
                break
            cur = cur.parent
        return None

    def _rel_paths(self, repo: Path, paths: list[str]) -> list[str]:
        out: list[str] = []
        for p in paths:
            abs_path = self._safe_path(p) if not Path(p).is_absolute() else Path(p).resolve()
            try:
                abs_path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"路径越界: {p}") from exc
            try:
                out.append(str(abs_path.relative_to(repo)).replace("\\", "/"))
            except ValueError:
                out.append(str(abs_path))
        return out


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _safe_ref(ref: str) -> bool:
    # 拒绝明显危险/注入字符
    if not ref or ref.startswith("-"):
        return False
    banned = set(" \t\n\r;&|`$<>")
    return not any(ch in banned for ch in ref)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."
