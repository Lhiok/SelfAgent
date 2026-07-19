"""从 Skill 调用中提取文件改动，供对话 UI 展示 diff。"""

from __future__ import annotations

import json
from difflib import unified_diff
from typing import Any

# 单次改动写入 SSE / 会话时的上限，避免巨型文件撑爆前端
DEFAULT_CHANGE_MAX_CHARS = 12000


def truncate_change_text(text: str, max_chars: int = DEFAULT_CHANGE_MAX_CHARS) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def build_unified_diff(
    path: str,
    old_text: str,
    new_text: str,
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> str:
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    if old_lines == new_lines:
        return ""
    lines = list(
        unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=3,
        )
    )
    diff = "\n".join(lines)
    if diff and not diff.endswith("\n"):
        diff += "\n"
    return truncate_change_text(diff, max_chars)


def normalize_change(
    change: dict[str, Any],
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> dict[str, Any] | None:
    if not isinstance(change, dict):
        return None
    kind = str(change.get("kind") or "").strip().lower()
    path = str(change.get("path") or "").strip()
    if not kind or not path:
        return None

    old_text = str(change.get("old_text") if change.get("old_text") is not None else "")
    new_text = str(change.get("new_text") if change.get("new_text") is not None else "")
    dest = str(change.get("dest") or "").strip()
    diff = str(change.get("diff") or "")

    if kind in {"write", "overwrite", "patch"} and not diff:
        diff = build_unified_diff(path, old_text, new_text, max_chars=max_chars)

    out: dict[str, Any] = {
        "kind": kind,
        "path": path.replace("\\", "/"),
        "old_text": truncate_change_text(old_text, max_chars),
        "new_text": truncate_change_text(new_text, max_chars),
        "diff": truncate_change_text(diff, max_chars),
    }
    if dest:
        out["dest"] = dest.replace("\\", "/")
    if change.get("truncated"):
        out["truncated"] = True
    return out


def change_from_skill_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("change")
    if isinstance(raw, dict):
        return normalize_change(raw)
    return None


def change_from_action_input(
    skill_name: str,
    action_input: str | None,
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> dict[str, Any] | None:
    """当 SkillResult.data 缺失时，从 Action Input JSON 回退解析。"""
    if str(skill_name or "").strip() != "local_file":
        return None
    try:
        payload = json.loads(action_input or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    op = str(payload.get("action") or "").strip().lower()
    path = str(payload.get("path") or "").strip()
    if not path:
        return None

    if op == "write":
        content = str(payload.get("content") if payload.get("content") is not None else "")
        return normalize_change(
            {
                "kind": "write",
                "path": path,
                "old_text": "",
                "new_text": content,
            },
            max_chars=max_chars,
        )
    if op == "patch":
        old = str(payload.get("old_text") if payload.get("old_text") is not None else "")
        new = str(payload.get("new_text") if payload.get("new_text") is not None else "")
        return normalize_change(
            {
                "kind": "patch",
                "path": path,
                "old_text": old,
                "new_text": new,
            },
            max_chars=max_chars,
        )
    if op == "move":
        dest = str(payload.get("dest") or "").strip()
        if not dest:
            return None
        return normalize_change(
            {"kind": "move", "path": path, "dest": dest, "old_text": "", "new_text": ""},
            max_chars=max_chars,
        )
    return None


def extract_change_from_call(
    *,
    skill_name: str,
    action_input: str | None,
    data: dict[str, Any] | None = None,
    ok: bool | None = True,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> dict[str, Any] | None:
    if ok is False:
        return None
    change = change_from_skill_data(data)
    if change is not None:
        return change
    return change_from_action_input(skill_name, action_input, max_chars=max_chars)


def change_group_key(change: dict[str, Any]) -> str:
    """同文件改动归并键：write/patch/overwrite 按 path；move 按 path+dest。"""
    kind = str(change.get("kind") or "").strip().lower()
    path = str(change.get("path") or "").replace("\\", "/")
    if kind == "move":
        dest = str(change.get("dest") or "").replace("\\", "/")
        return f"move|{path}|{dest}"
    return f"file|{path}"


def _diff_body_without_headers(diff: str) -> str:
    lines = [
        ln
        for ln in str(diff or "").splitlines()
        if not ln.startswith("--- ") and not ln.startswith("+++ ")
    ]
    return "\n".join(lines).strip("\n")


def merge_unified_diffs(
    diffs: list[str],
    path: str,
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> str:
    """合并多个 unified diff 的 hunk（去掉重复文件头）。"""
    path = path.replace("\\", "/")
    bodies: list[str] = []
    seen: set[str] = set()
    for diff in diffs:
        body = _diff_body_without_headers(diff)
        if not body or body in seen:
            continue
        seen.add(body)
        bodies.append(body)
    if not bodies:
        return ""
    merged = f"--- a/{path}\n+++ b/{path}\n" + "\n".join(bodies)
    if not merged.endswith("\n"):
        merged += "\n"
    return truncate_change_text(merged, max_chars)


_CONTENT_KINDS = frozenset({"write", "overwrite", "patch"})


def merge_changes_by_path(
    changes: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> list[dict[str, Any]]:
    """
    将同一文件的多处改动合并为一条展示。

    - write / overwrite / patch：按 path 合并，diff hunk 拼接
    - move：按 path+dest 独立
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for raw in changes or []:
        if not isinstance(raw, dict):
            continue
        change = normalize_change(raw, max_chars=max_chars) or dict(raw)
        path = str(change.get("path") or "").replace("\\", "/")
        if not path and change.get("kind") != "move":
            continue
        key = change_group_key(change)
        if key not in grouped:
            item = dict(change)
            item["path"] = path
            item["patch_count"] = 1
            grouped[key] = item
            order.append(key)
            continue

        existing = grouped[key]
        existing["patch_count"] = int(existing.get("patch_count") or 1) + 1
        kind_a = str(existing.get("kind") or "")
        kind_b = str(change.get("kind") or "")
        if kind_a in _CONTENT_KINDS and kind_b in _CONTENT_KINDS and kind_a != kind_b:
            # 混合写入类型时统一标为 patch（多处改动）
            existing["kind"] = "patch"

        diffs = [str(existing.get("diff") or ""), str(change.get("diff") or "")]
        existing["diff"] = merge_unified_diffs(diffs, path, max_chars=max_chars)

        # 片段文本仅作兜底；多 hunk 时以合并 diff 为准
        if change.get("old_text") and not existing.get("old_text"):
            existing["old_text"] = change.get("old_text")
        if change.get("new_text"):
            # 保留最后一次 new_text 作无 diff 时的回退
            existing["new_text"] = change.get("new_text")
        if change.get("truncated") or existing.get("truncated"):
            existing["truncated"] = True

    return [grouped[k] for k in order]


def collect_changes_from_steps(
    steps: list[Any],
    *,
    max_chars: int = DEFAULT_CHANGE_MAX_CHARS,
) -> list[dict[str, Any]]:
    """从 AgentStep 列表收集文件改动，同一文件多处修改合并为一条。"""
    out: list[dict[str, Any]] = []
    for step in steps or []:
        calls = getattr(step, "calls", None) or []
        for call in calls:
            change = extract_change_from_call(
                skill_name=getattr(call, "action", "") or "",
                action_input=getattr(call, "action_input", None),
                data=getattr(call, "data", None),
                ok=getattr(call, "ok", True),
                max_chars=max_chars,
            )
            if change:
                out.append(change)
    return merge_changes_by_path(out, max_chars=max_chars)
