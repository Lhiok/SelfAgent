"""Python 工具链支持。"""

from __future__ import annotations

import sys
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

logger = get_logger("skills.python")


class PythonSkill(Skill):
    name = "python"
    description = (
        "Python 项目工具链：运行脚本、pytest、pip 安装、python -m 模块、语法检查、查版本。"
        "默认使用当前解释器；可配置 skills.python.python_bin。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "test", "install", "module", "compile", "version"],
                "default": "test",
            },
            "cwd": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的工作目录",
            },
            "file": {
                "type": "string",
                "description": "run/compile 时的 .py 文件（相对 cwd）",
            },
            "module": {
                "type": "string",
                "description": "action=module 时的模块名，如 pytest / pip",
            },
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "action=install 时安装的包名列表",
            },
            "requirements": {
                "type": "string",
                "description": "action=install 时的 requirements 文件路径",
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
        python_bin: str | None = None,
        default_timeout: float = 180.0,
        max_output_chars: int = 30000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.python_bin = python_bin
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self.enabled = enabled

    def _python(self) -> str | None:
        if self.python_bin:
            return which_bin(self.python_bin) or self.python_bin
        # 优先当前进程解释器（venv 友好）
        if sys.executable:
            return sys.executable
        return which_bin("python", "python3", "py")

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="python 已在配置中禁用")

        action = str(kwargs.get("action") or "test").strip().lower()
        cwd, err = resolve_cwd(self.root, str(kwargs.get("cwd") or "."))
        if err or cwd is None:
            return SkillResult(ok=False, output=err or "cwd 无效")

        py = self._python()
        if py is None:
            return SkillResult(ok=False, output="未找到 Python 解释器")

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        extra = extra_args_list(kwargs)

        if action == "version":
            return run_command(
                [py, "--version"],
                cwd=cwd,
                timeout=min(timeout, 30),
                max_output_chars=2000,
                label="python version",
            )

        if action == "run":
            rel = str(kwargs.get("file") or "").strip()
            if not rel:
                return SkillResult(ok=False, output="run 需要 file 参数")
            path, perr = resolve_under(self.root, cwd, rel)
            if perr or path is None:
                return SkillResult(ok=False, output=perr or "file 无效")
            if not path.is_file():
                return SkillResult(ok=False, output=f"文件不存在: {rel}")
            argv = [py, str(path), *extra]
        elif action == "compile":
            rel = str(kwargs.get("file") or "").strip()
            if not rel:
                return SkillResult(ok=False, output="compile 需要 file 参数")
            path, perr = resolve_under(self.root, cwd, rel)
            if perr or path is None:
                return SkillResult(ok=False, output=perr or "file 无效")
            if not path.is_file():
                return SkillResult(ok=False, output=f"文件不存在: {rel}")
            argv = [py, "-m", "py_compile", str(path), *extra]
        elif action == "test":
            argv = [py, "-m", "pytest", *extra] if extra else [py, "-m", "pytest", "-q"]
        elif action == "module":
            mod = str(kwargs.get("module") or "").strip()
            if not mod:
                return SkillResult(ok=False, output="module 需要 module 参数")
            if any(c in mod for c in (" ", "/", "\\", "..")):
                return SkillResult(ok=False, output="module 名不合法")
            argv = [py, "-m", mod, *extra]
        elif action == "install":
            argv = self._install_argv(py, cwd, kwargs, extra)
            if isinstance(argv, SkillResult):
                return argv
        else:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        logger.notice(f"python: {' '.join(argv)} cwd={cwd}")
        result = run_command(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=self.max_output_chars,
            label=f"python {action}",
        )
        if result.data is not None:
            result.data["action"] = action
        return result

    def _install_argv(
        self,
        py: str,
        cwd: Path,
        kwargs: dict[str, Any],
        extra: list[str],
    ) -> list[str] | SkillResult:
        req = str(kwargs.get("requirements") or "").strip()
        packages = kwargs.get("packages")
        pkg_list = [str(p).strip() for p in packages] if isinstance(packages, list) else []
        pkg_list = [p for p in pkg_list if p]
        if req:
            path, err = resolve_under(self.root, cwd, req)
            if err or path is None:
                return SkillResult(ok=False, output=err or "requirements 无效")
            if not path.is_file():
                return SkillResult(ok=False, output=f"requirements 不存在: {req}")
            return [py, "-m", "pip", "install", "-r", str(path), *extra]
        if pkg_list:
            # 禁止奇怪字符，降低注入风险
            for p in pkg_list:
                if any(c in p for c in (";", "|", "&", "`", "$", "\n")):
                    return SkillResult(ok=False, output=f"非法包名: {p}")
            return [py, "-m", "pip", "install", *pkg_list, *extra]
        return SkillResult(ok=False, output="install 需要 packages 或 requirements")
