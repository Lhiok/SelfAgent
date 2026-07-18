"""C# / .NET 工具链支持。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from log import get_logger
from skills.base import Skill, SkillResult
from skills.lang_runtime import (
    extra_args_list,
    resolve_cwd,
    resolve_under,
    run_command,
    which_bin,
)

logger = get_logger("skills.csharp")


class CsharpSkill(Skill):
    name = "csharp"
    description = (
        "C# / .NET 项目工具链：restore/build/test/run、dotnet format、查 SDK 版本。"
        "比专用的 dotnet_build 覆盖更全（含 run/format）；需本机已安装 .NET SDK。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["restore", "build", "test", "run", "format", "version"],
                "default": "build",
            },
            "cwd": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的工作目录",
            },
            "project": {
                "type": "string",
                "description": "相对 cwd 的 .sln/.csproj，空则在 cwd 执行",
            },
            "configuration": {
                "type": "string",
                "default": "Debug",
                "description": "Debug/Release 等（build/test/run）",
            },
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
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
            return SkillResult(ok=False, output="csharp 已在配置中禁用")

        action = str(kwargs.get("action") or "build").strip().lower()
        if action not in {"restore", "build", "test", "run", "format", "version"}:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        dotnet = which_bin(self.dotnet_bin, "dotnet", "dotnet.exe")
        if dotnet is None:
            return SkillResult(
                ok=False,
                output=f"未找到 {self.dotnet_bin}，请安装 .NET SDK 或配置 skills.csharp.dotnet_bin",
            )

        cwd, err = resolve_cwd(self.root, str(kwargs.get("cwd") or "."))
        if err or cwd is None:
            return SkillResult(ok=False, output=err or "cwd 无效")

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        extra = extra_args_list(kwargs)

        if action == "version":
            result = run_command(
                [dotnet, "--info"],
                cwd=cwd,
                timeout=min(timeout, 60),
                max_output_chars=self.max_output_chars,
                label="csharp version",
            )
            if result.data is not None:
                result.data["action"] = "version"
            return result

        argv = [dotnet, action]
        project = str(kwargs.get("project") or "").strip()
        if project:
            path, perr = resolve_under(self.root, cwd, project)
            if perr or path is None:
                return SkillResult(ok=False, output=perr or "project 无效")
            if not path.exists():
                return SkillResult(ok=False, output=f"项目文件不存在: {project}")
            argv.append(str(path))

        if action in {"build", "test", "run"}:
            cfg = str(kwargs.get("configuration") or "Debug").strip() or "Debug"
            argv.extend(["-c", cfg])

        if action == "format":
            # dotnet format [<PROJECT>]
            pass

        argv.extend(extra)
        logger.notice(f"csharp: {' '.join(argv)} cwd={cwd}")
        result = run_command(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=self.max_output_chars,
            label=f"csharp {action}",
        )
        if result.data is not None:
            result.data["action"] = action
        return result
