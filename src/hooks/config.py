"""加载 hooks 配置：config.yaml hooks: 或 .selfagent/hooks.yaml。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import config as cfg
from hooks.events import HookEvent
from hooks.types import HookDefinition
from log import get_logger

logger = get_logger("hooks.config")


def load_hook_definitions(
    *,
    section: dict[str, Any] | None = None,
    workdir: str | Path | None = None,
) -> list[HookDefinition]:
    items: list[Any] = []
    hooks_cfg = section if section is not None else (cfg.get_section("hooks", {}) or {})
    if isinstance(hooks_cfg, dict):
        # 形状 A: hooks: { PreToolUse: [ {...}, ... ], ... }
        # 形状 B: hooks: { entries: [ {event, ...}, ... ] }
        if "entries" in hooks_cfg:
            items.extend(hooks_cfg.get("entries") or [])
        else:
            for key, val in hooks_cfg.items():
                if key in {"enabled", "entries"}:
                    continue
                event = HookEvent.parse(key)
                if event is None:
                    continue
                for entry in val or []:
                    if isinstance(entry, dict):
                        merged = dict(entry)
                        merged.setdefault("event", event.value)
                        items.append(merged)
                    elif isinstance(entry, str):
                        items.append(
                            {"event": event.value, "runner": "command", "command": entry}
                        )
    elif isinstance(hooks_cfg, list):
        items.extend(hooks_cfg)

    # 文件覆盖/补充
    root = Path(workdir) if workdir else Path.cwd()
    file_path = root / ".selfagent" / "hooks.yaml"
    if file_path.is_file():
        try:
            raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and "entries" in raw:
                items.extend(raw.get("entries") or [])
            elif isinstance(raw, dict):
                for key, val in raw.items():
                    event = HookEvent.parse(key)
                    if event is None:
                        continue
                    for entry in val or []:
                        if isinstance(entry, dict):
                            merged = dict(entry)
                            merged.setdefault("event", event.value)
                            items.append(merged)
            elif isinstance(raw, list):
                items.extend(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"读取 {file_path} 失败: {exc}")

    out: list[HookDefinition] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = HookEvent.parse(item.get("event"))
        if event is None:
            continue
        runner = str(item.get("runner") or item.get("type") or "command").strip().lower()
        out.append(
            HookDefinition(
                event=event,
                runner=runner,
                matcher=str(item.get("matcher") or item.get("tool_name") or "*"),
                command=str(item.get("command") or ""),
                url=str(item.get("url") or ""),
                timeout=float(item.get("timeout") or 30),
                env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                inject_as=str(item.get("inject_as") or "user"),
            )
        )
    return out
