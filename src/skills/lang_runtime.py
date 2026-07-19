"""语言类 Skill 共用：路径校验与受控子进程执行。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from skills.base import SkillResult


def resolve_cwd(root: Path, cwd_rel: str) -> tuple[Path | None, str | None]:
    rel = (cwd_rel or ".").strip() or "."
    try:
        cwd = (root / rel).resolve()
        cwd.relative_to(root)
    except (OSError, ValueError):
        return None, f"cwd 越界: {rel}"
    if not cwd.is_dir():
        return None, f"工作目录不存在: {cwd}"
    return cwd, None


def resolve_under(root: Path, base: Path, rel: str) -> tuple[Path | None, str | None]:
    text = (rel or "").strip()
    if not text:
        return None, "路径不能为空"
    path = (base / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None, f"路径越界: {rel}"
    return path, None


def which_bin(*candidates: str) -> str | None:
    for name in candidates:
        if not name:
            continue
        found = shutil.which(name)
        if found:
            return found
    return None


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
    label: str,
    env: dict[str, str] | None = None,
) -> SkillResult:
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
            env=env if env is not None else {**os.environ},
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return SkillResult(ok=False, output=f"{label} 超时（{timeout}s）")
    except OSError as exc:
        return SkillResult(ok=False, output=f"无法启动命令: {exc}")

    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    if len(out) > max_output_chars:
        out = out[: max_output_chars - 1] + "…"
    head = f"{label} exit={proc.returncode}"
    return SkillResult(
        ok=proc.returncode == 0,
        output=f"{head}\n{out}" if out else head,
        data={
            "argv": argv,
            "exit_code": proc.returncode,
            "cwd": str(cwd),
        },
    )


def extra_args_list(kwargs: dict[str, Any]) -> list[str]:
    extra = kwargs.get("extra_args")
    if not isinstance(extra, list):
        return []
    return [str(x) for x in extra if str(x).strip()]
