"""权限模式变换（对齐 PermissionMode + acceptEdits / bypass / dontAsk / plan）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from permission.types import PermissionBehavior, PermissionDecision, PermissionMode

# accept_edits 下自动放行的 local_file 写操作
EDIT_ACTIONS = frozenset({"write", "patch", "move", "mkdir", "delete", "create", "append"})


def is_edit_action(skill: str, action: str | None) -> bool:
    if (skill or "").strip().lower() != "local_file":
        return False
    if not action:
        return False
    return action.strip().lower() in EDIT_ACTIONS


def path_under_workdir(arguments: dict[str, Any], workdir: Path | None) -> bool:
    """若无 path 参数或无 workdir，视为可编辑；有 path 则须落在 workdir 下。"""
    if workdir is None:
        return True
    path_keys = ("path", "file", "target", "dst", "dest", "to")
    raw = None
    for key in path_keys:
        if key in arguments and arguments[key] is not None:
            raw = str(arguments[key]).strip()
            if raw:
                break
    if not raw:
        return True
    try:
        root = workdir.expanduser().resolve()
        target = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        target.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def apply_dont_ask(decision: PermissionDecision) -> PermissionDecision:
    if decision.behavior is PermissionBehavior.ASK:
        return PermissionDecision.deny(
            f"dont_ask: {decision.reason}",
            matched_rule=decision.matched_rule,
            suggestions=decision.suggestions,
        )
    return decision


def mode_may_allow(
    mode: PermissionMode,
    *,
    skill: str,
    action: str | None,
    arguments: dict[str, Any],
    workdir: Path | None,
) -> PermissionDecision | None:
    """模式快速放行；返回 None 表示不介入。"""
    if mode is PermissionMode.BYPASS:
        return PermissionDecision.allow("mode=bypass 放行", matched_rule="mode:bypass")

    if mode is PermissionMode.ACCEPT_EDITS and is_edit_action(skill, action):
        if path_under_workdir(arguments, workdir):
            return PermissionDecision.allow(
                "mode=accept_edits 放行编辑",
                matched_rule="mode:accept_edits",
            )
    return None
