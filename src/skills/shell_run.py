"""受控执行本地命令（强限制白名单）。"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.shell_run")

# 默认允许的可执行文件 / 命令前缀（小写比较）
_DEFAULT_ALLOW = [
    "python",
    "python3",
    "py",
    "pip",
    "pytest",
    "git",
    "dir",
    "ls",
    "echo",
    "type",
    "cat",
    "where",
    "which",
]

_DEFAULT_DENY_SUBSTR = [
    "rm -rf",
    "del /f",
    "format ",
    "shutdown",
    "powershell -enc",
    "curl |",
    "wget |",
    ">",
    "|",
    "&&",
    ";",
    "`",
]


class ShellRunSkill(Skill):
    name = "shell_run"
    description = (
        "在受限工作目录内执行白名单命令（如 pytest、git status）。"
        "禁止管道/重定向/危险指令；适合跑测试与查看状态。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run"],
                "default": "run",
            },
            "command": {
                "type": "string",
                "description": "要执行的命令行（需命中白名单）；与 argv 二选一",
            },
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参数数组形式（推荐，避免 Windows 路径转义问题）",
            },
            "cwd": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的工作目录",
            },
            "timeout": {
                "type": "number",
                "default": 60,
                "description": "超时秒数",
            },
        },
        "required": [],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        allow_commands: list[str] | None = None,
        deny_substrings: list[str] | None = None,
        default_timeout: float = 60.0,
        max_output_chars: int = 20000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.allow_commands = [c.lower() for c in (allow_commands or _DEFAULT_ALLOW)]
        self.deny_substrings = [d.lower() for d in (deny_substrings or _DEFAULT_DENY_SUBSTR)]
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="shell_run 已在配置中禁用")

        action = str(kwargs.get("action") or "run").strip().lower()
        if action != "run":
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        cwd_rel = str(kwargs.get("cwd") or ".").strip() or "."

        argv_raw = kwargs.get("argv")
        command = str(kwargs.get("command") or "").strip()
        if isinstance(argv_raw, list) and argv_raw:
            argv = [str(x) for x in argv_raw if str(x).strip() != ""]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else " ".join(
                shlex.quote(a) for a in argv
            )
        elif command:
            denied = self._check_denied(command)
            if denied:
                return SkillResult(ok=False, output=denied)
            try:
                argv = shlex.split(command, posix=(os.name != "nt"))
            except ValueError as exc:
                return SkillResult(ok=False, output=f"命令解析失败: {exc}")
        else:
            return SkillResult(ok=False, output="shell_run 需要 command 或 argv")

        if not argv:
            return SkillResult(ok=False, output="命令为空")

        # argv 模式也检查拼接后的禁止片段
        denied = self._check_denied(command)
        if denied:
            return SkillResult(ok=False, output=denied)

        exe = Path(argv[0]).name.lower()
        if exe.endswith(".exe"):
            exe = exe[:-4]
        if exe not in self.allow_commands and argv[0].lower() not in self.allow_commands:
            return SkillResult(
                ok=False,
                output=f"命令不在白名单: {argv[0]}；允许: {self.allow_commands}",
            )

        try:
            cwd = self._safe_path(cwd_rel)
        except ValueError as exc:
            return SkillResult(ok=False, output=str(exc))
        if not cwd.is_dir():
            return SkillResult(ok=False, output=f"工作目录不存在: {cwd_rel}")

        logger.notice(f"shell_run: {command!r} cwd={cwd}")
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SkillResult(ok=False, output=f"命令超时（>{timeout}s）: {command}")
        except OSError as exc:
            return SkillResult(ok=False, output=f"执行失败: {exc}")

        stdout = _clip(completed.stdout or "", self.max_output_chars)
        stderr = _clip(completed.stderr or "", self.max_output_chars)
        payload = {
            "command": command,
            "argv": argv,
            "cwd": str(cwd.relative_to(self.root)).replace("\\", "/") or ".",
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        ok = completed.returncode == 0
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if not ok:
            return SkillResult(ok=False, output=text, data=payload)
        return SkillResult(ok=True, output=text, data=payload)

    def _check_denied(self, command: str) -> str | None:
        lower = command.lower()
        for item in self.deny_substrings:
            if item in lower:
                return f"命令包含禁止片段: {item!r}"
        return None

    def _safe_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径越界，禁止访问 root 之外: {rel}") from exc
        return candidate


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."
