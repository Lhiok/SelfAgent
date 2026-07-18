"""Node.js / npm 工具链支持。"""

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

logger = get_logger("skills.nodejs")

_PM_BINS = {
    "npm": ("npm", "npm.cmd"),
    "pnpm": ("pnpm", "pnpm.cmd"),
    "yarn": ("yarn", "yarn.cmd"),
}


class NodejsSkill(Skill):
    name = "nodejs"
    description = (
        "Node.js 项目工具链：安装依赖、跑脚本/测试/构建、执行 .js/.ts（via npx ts-node 可选）、查版本。"
        "需本机已安装 Node.js；包管理器默认 npm，可切 pnpm/yarn。"
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["install", "test", "build", "script", "run", "version"],
                "default": "test",
            },
            "cwd": {
                "type": "string",
                "default": ".",
                "description": "相对 root 的工作目录（含 package.json）",
            },
            "package_manager": {
                "type": "string",
                "enum": ["npm", "pnpm", "yarn"],
                "default": "npm",
            },
            "script": {
                "type": "string",
                "description": "action=script 时的 npm script 名",
            },
            "file": {
                "type": "string",
                "description": "action=run 时相对 cwd 的入口文件",
            },
            "ci": {
                "type": "boolean",
                "default": False,
                "description": "action=install 时使用 npm ci / pnpm install --frozen-lockfile",
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
        node_bin: str = "node",
        default_pm: str = "npm",
        default_timeout: float = 180.0,
        max_output_chars: int = 30000,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.node_bin = node_bin or "node"
        self.default_pm = (default_pm or "npm").lower()
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self.enabled = enabled

    def run(self, **kwargs: Any) -> SkillResult:
        if not self.enabled:
            return SkillResult(ok=False, output="nodejs 已在配置中禁用")

        action = str(kwargs.get("action") or "test").strip().lower()
        cwd, err = resolve_cwd(self.root, str(kwargs.get("cwd") or "."))
        if err or cwd is None:
            return SkillResult(ok=False, output=err or "cwd 无效")

        timeout = float(kwargs.get("timeout") or self.default_timeout)
        extra = extra_args_list(kwargs)
        pm_name = str(kwargs.get("package_manager") or self.default_pm).strip().lower()
        if pm_name not in _PM_BINS:
            return SkillResult(ok=False, output=f"不支持的 package_manager: {pm_name}")

        if action == "version":
            return self._version(cwd, timeout)

        if action == "run":
            return self._run_file(cwd, kwargs, timeout, extra)

        pm = which_bin(*_PM_BINS[pm_name])
        if pm is None:
            return SkillResult(
                ok=False,
                output=f"未找到 {pm_name}，请安装 Node.js 包管理器或改用其他 package_manager",
            )

        if action == "install":
            argv = self._install_argv(pm, pm_name, bool(kwargs.get("ci")))
        elif action == "test":
            if pm_name == "npm" and extra:
                argv = [pm, "test", "--", *extra]
            else:
                argv = [pm, "test", *extra]
        elif action == "build":
            argv = [pm, "run", "build", *extra] if pm_name != "yarn" else [pm, "build", *extra]
        elif action == "script":
            script = str(kwargs.get("script") or "").strip()
            if not script:
                return SkillResult(ok=False, output="script 需要 script 参数（如 start）")
            argv = [pm, script, *extra] if pm_name == "yarn" else [pm, "run", script, *extra]
        else:
            return SkillResult(ok=False, output=f"不支持的 action: {action}")

        logger.notice(f"nodejs: {' '.join(argv)} cwd={cwd}")
        result = run_command(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=self.max_output_chars,
            label=f"nodejs {action}",
        )
        if result.data is not None:
            result.data["action"] = action
            result.data["package_manager"] = pm_name
        return result

    def _install_argv(self, pm: str, pm_name: str, ci: bool) -> list[str]:
        if pm_name == "npm":
            return [pm, "ci"] if ci else [pm, "install"]
        if pm_name == "pnpm":
            return [pm, "install", "--frozen-lockfile"] if ci else [pm, "install"]
        return [pm, "install", "--frozen-lockfile"] if ci else [pm, "install"]

    def _run_file(
        self,
        cwd: Path,
        kwargs: dict[str, Any],
        timeout: float,
        extra: list[str],
    ) -> SkillResult:
        node = which_bin(self.node_bin, "node", "node.exe")
        if node is None:
            return SkillResult(ok=False, output="未找到 node，请安装 Node.js")
        rel = str(kwargs.get("file") or "").strip()
        if not rel:
            return SkillResult(ok=False, output="run 需要 file 参数")
        path, err = resolve_under(self.root, cwd, rel)
        if err or path is None:
            return SkillResult(ok=False, output=err or "file 无效")
        if not path.is_file():
            return SkillResult(ok=False, output=f"文件不存在: {rel}")
        argv = [node, str(path), *extra]
        logger.notice(f"nodejs run: {' '.join(argv)}")
        result = run_command(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_chars=self.max_output_chars,
            label="nodejs run",
        )
        if result.data is not None:
            result.data["action"] = "run"
        return result

    def _version(self, cwd: Path, timeout: float) -> SkillResult:
        node = which_bin(self.node_bin, "node", "node.exe")
        parts: list[str] = []
        ok = True
        if node:
            r = run_command(
                [node, "-v"],
                cwd=cwd,
                timeout=min(timeout, 30),
                max_output_chars=2000,
                label="node -v",
            )
            parts.append(r.output)
            ok = ok and r.ok
        else:
            parts.append("node: 未安装")
            ok = False
        for pm_name in ("npm", "pnpm", "yarn"):
            pm = which_bin(*_PM_BINS[pm_name])
            if not pm:
                continue
            r = run_command(
                [pm, "-v"],
                cwd=cwd,
                timeout=min(timeout, 30),
                max_output_chars=2000,
                label=f"{pm_name} -v",
            )
            parts.append(f"{pm_name}: {(r.output or '').splitlines()[-1] if r.ok else 'error'}")
        return SkillResult(ok=ok, output="\n".join(parts), data={"action": "version"})
