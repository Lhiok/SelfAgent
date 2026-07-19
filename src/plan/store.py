"""Plan 落盘：.md + .json + hash。"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from plan.model import Plan, PlanStep


def resolve_plans_dir(base: str | Path | None = None) -> Path:
    import config as cfg

    if base is not None and str(base).strip():
        path = Path(base).expanduser().resolve()
    else:
        plan_cfg = cfg.get_section("plan", {}) or {}
        wf_cfg = cfg.get_section("workflow", {}) or {}
        raw = plan_cfg.get("plans_dir") or wf_cfg.get("plans_dir") or ".selfagent/plans"
        path = Path(str(raw)).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def plan_hash(plan: Plan) -> str:
    payload = json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def save_draft(
    plan: Plan,
    base: str | Path | None = None,
    *,
    plan_id: str | None = None,
) -> tuple[str, str, Path]:
    root = resolve_plans_dir(base)
    pid = plan_id or uuid.uuid4().hex[:12]
    h = plan_hash(plan)
    md_path = root / f"{pid}.md"
    md_path.write_text(render_markdown(plan, pid, h), encoding="utf-8")
    meta = root / f"{pid}.json"
    meta.write_text(
        json.dumps(
            {"id": pid, "hash": h, "plan": plan.to_dict()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return pid, h, md_path


def load_draft(plan_id: str, base: str | Path | None = None) -> tuple[Plan, str] | None:
    root = resolve_plans_dir(base)
    meta = root / f"{plan_id}.json"
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    plan_data = data.get("plan") or {}
    plan = Plan(
        summary=str(plan_data.get("summary") or ""),
        thought=str(plan_data.get("thought") or ""),
        steps=[
            PlanStep(
                index=int(s.get("index") or i + 1),
                skill=str(s.get("skill") or ""),
                arguments=dict(s.get("arguments") or {})
                if isinstance(s.get("arguments"), dict)
                else {},
                why=str(s.get("why") or ""),
                raw_input=str(s.get("raw_input") or ""),
            )
            for i, s in enumerate(plan_data.get("steps") or [])
            if isinstance(s, dict)
        ],
        raw_text=str(plan_data.get("raw_text") or ""),
    )
    return plan, str(data.get("hash") or "")


def render_markdown(plan: Plan, plan_id: str, h: str) -> str:
    lines = [
        f"# Plan {plan_id}",
        "",
        f"hash: `{h}`",
        "",
        plan.summary or "(no summary)",
        "",
        "## Steps",
        "",
    ]
    for step in plan.steps:
        payload = json.dumps(step.arguments, ensure_ascii=False)
        why = f" — {step.why}" if step.why else ""
        lines.append(f"{step.index}. `{step.skill}` `{payload}`{why}")
    return "\n".join(lines) + "\n"
