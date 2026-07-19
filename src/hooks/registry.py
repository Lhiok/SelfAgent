"""Hook 注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hooks.config import load_hook_definitions
from hooks.events import ALL_HOOK_EVENTS, HookEvent
from hooks.types import HookDefinition


class HookRegistry:
    def __init__(self, definitions: list[HookDefinition] | None = None) -> None:
        self._defs: list[HookDefinition] = list(definitions or [])

    @classmethod
    def from_config(
        cls,
        *,
        section: dict[str, Any] | None = None,
        workdir: str | Path | None = None,
    ) -> "HookRegistry":
        return cls(load_hook_definitions(section=section, workdir=workdir))

    @classmethod
    def empty(cls) -> "HookRegistry":
        return cls([])

    def add(self, definition: HookDefinition) -> None:
        self._defs.append(definition)

    def clear(self) -> None:
        self._defs.clear()

    def all_events(self) -> tuple[HookEvent, ...]:
        return ALL_HOOK_EVENTS

    def list_for(self, event: HookEvent | str) -> list[HookDefinition]:
        ev = HookEvent.parse(event) if not isinstance(event, HookEvent) else event
        if ev is None:
            return []
        return [d for d in self._defs if d.event is ev]

    def match(self, event: HookEvent, tool_name: str | None = None) -> list[HookDefinition]:
        name = (tool_name or "").strip()
        out: list[HookDefinition] = []
        for d in self.list_for(event):
            m = (d.matcher or "*").strip()
            if m in {"*", ""}:
                out.append(d)
            elif name and (m == name or name.startswith(m.rstrip("*"))):
                out.append(d)
            elif name and m.endswith("*") and name.startswith(m[:-1]):
                out.append(d)
        return out

    def __len__(self) -> int:
        return len(self._defs)


_GLOBAL: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    global _GLOBAL
    if _GLOBAL is None:
        _GLOBAL = HookRegistry.from_config()
    return _GLOBAL


def set_hook_registry(registry: HookRegistry | None) -> None:
    global _GLOBAL
    _GLOBAL = registry


def reset_hook_registry() -> None:
    set_hook_registry(None)
