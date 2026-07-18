"""在工作目录内执行 dotnet build / test / restore。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult

logger = get_logger("skills.dotnet_build")


class DotnetBuildSkill(Skill):
    name = "dotnet_build"
    description = (
        "在项目工作目录执行 dotnet restore/build/test，用于验证 C#/.NET 改动是否可编译。"
        "可选指定 csproj/sln；需本机已安装 dotnet CLI。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["build", "test", "restore"],
                "default": "build",
            },
            "project": {
                "type": "string",
                "description": "相对 root 的 .sln/.csproj，空则在 cwd 执行",
            },
            "cwd": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的工作目录",
            },
            "configuration": {
                "type": "string",
                "default": "Debug",
                "description": "Debug/Release 等",
            },
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "附加参数，如 [\"--no-restore\"]",
            },
            "timeout": {"type": "number", "default": 180},
        },
        "required": [],
    }

    def __init__(
        self,
        root: str | Path = ".",
        *,
        dotnet_bin: str = "dotnet",
        default_timeout: float = 180.0,
        max_output_chars: int = 30000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.dotnet_bin = dotnet_bin or "dotnet"
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="dotnet_build 已在配置中禁用")

        action = str(kwargs.get("action") or "build").strip().lower()
        if action not in {"build", "test", "restore"}:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        if shutil.which(self.dotnet_bin) is None:
            return SkillResult(
                ok=False,
                output=f"未找到 {self.dotnet_bin}，请安装 .NET SDK 或配置 skills.dotnet_build.dotnet_bin",
            )

        cwd_rel = str(kwargs.get("cwd") or ".").strip() or "."
        try:
            cwd = (self.root / cwd_rel).resolve()
            cwd.relative_to(self.root)
        except (OSError, ValueError):
            return SkillResult(ok=False, output=f"cwd 越界: {cwd_rel}")
        if not cwd.is_dir():
            return SkillResult(ok=False, output=f"工作目录不存在: {cwd}")

        argv = [self.dotnet_bin, action]
        project = str(kwargs.get("project") or "").strip()
        if project:
            proj_path = (cwd / project).resolve() if not Path(project).is_absolute() else Path(project)
            try:
                proj_path.relative_to(self.root)
            except ValueError:
                return SkillResult(ok=False, output=f"project 越界: {project}")
            if not proj_path.exists():
                return SkillResult(ok=False, output=f"项目文件不存在: {proj_path}")
            argv.append(str(proj_path))

        if action in {"build", "test"}:
            cfg_name = str(kwargs.get("configuration") or "Debug").strip() or "Debug"
            argv.extend(["-c", cfg_name])

        extra = kwargs.get("extra_args")
        if isinstance(extra, list):
            argv.extend(str(x) for x in extra if str(x).strip())

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        logger.notice(f"dotnet_build: {' '.join(argv)} cwd={cwd}")
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env={**os.environ},
            )
        except subprocess.TimeoutExpired:
            return SkillResult(ok=False, output=f"dotnet {action} 超时（{timeout}s）")
        except OSError as exc:
            return SkillResult(ok=False, output=f"无法启动 dotnet: {exc}")

        out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
        if len(out) > self.max_output_chars:
            out = out[: self.max_output_chars - 1] + "…"
        ok = proc.returncode == 0
        head = f"dotnet {action} exit={proc.returncode}"
        return SkillResult(
            ok=ok,
            output=f"{head}\n{out}" if out else head,
            data={"action": action, "exit_code": proc.returncode, "cwd": str(cwd)},
        )
