"""WorkflowRun 持久化。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from workflow.types import WorkflowRun


def runs_dir(base: str | Path) -> Path:
    path = Path(base).expanduser().resolve() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def save_run(run: WorkflowRun, base: str | Path) -> Path:
    target = runs_dir(base) / f"{run.id}.json"
    target.write_text(
        json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def load_run(run_id: str, base: str | Path) -> WorkflowRun | None:
    target = runs_dir(base) / f"{run_id}.json"
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return WorkflowRun.from_dict(data)


def list_runs(base: str | Path, *, limit: int = 50) -> list[WorkflowRun]:
    root = runs_dir(base)
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[WorkflowRun] = []
    for path in files[: max(0, limit)]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(WorkflowRun.from_dict(data))
    return out
