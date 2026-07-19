"""加载 .selfagent/workflows/*.yaml。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from log import get_logger
from workflow.types import WorkflowDef, WorkflowStep

logger = get_logger("workflow.loader")


def load_workflows(directory: str | Path) -> dict[str, WorkflowDef]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return {}
    out: dict[str, WorkflowDef] = {}
    for path in sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml")):
        try:
            data = _read_yaml(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"无法加载工作流 {path}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or path.stem).strip()
        if not name:
            continue
        steps_raw = data.get("steps") or []
        steps: list[WorkflowStep] = []
        for i, item in enumerate(steps_raw):
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or f"step{i + 1}")
            steps.append(
                WorkflowStep(
                    id=sid,
                    skill=item.get("skill"),
                    input=dict(item.get("input") or {})
                    if isinstance(item.get("input"), dict)
                    else {},
                    type=str(item.get("type") or ("agent" if item.get("prompt") else "skill")),
                    prompt=str(item.get("prompt") or ""),
                    depends_on=[str(x) for x in (item.get("depends_on") or [])],
                    why=str(item.get("why") or ""),
                )
            )
        out[name] = WorkflowDef(
            name=name,
            description=str(data.get("description") or ""),
            mode=str(data.get("mode") or "agent"),
            on_error=str(data.get("on_error") or "stop"),
            steps=steps,
            source_path=str(path),
        )
    return out


def _read_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return _minimal_yaml(text)


def _minimal_yaml(text: str) -> dict[str, Any]:
    """无 PyYAML 时的极简解析（仅支持本仓库示例子集）。"""
    # Prefer json if file is json-compatible; else try PyYAML-less shallow parse
    import json

    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    # Fallback: require PyYAML for full YAML
    raise RuntimeError(
        "加载 YAML 工作流需要 PyYAML：pip install pyyaml"
    )
