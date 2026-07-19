"""斜杠 / 工作流命令注册。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from workflow.loader import load_workflows
from workflow.types import WorkflowDef

ContextKind = Literal["inline", "fork", "workflow"]


@dataclass
class SlashCommand:
    name: str
    description: str = ""
    kind: ContextKind = "inline"
    workflow: WorkflowDef | None = None
    prompt_template: str = ""
    handler: Callable[..., Any] | None = None


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name.strip().lstrip("/")] = cmd

    def get(self, name: str) -> SlashCommand | None:
        return self._commands.get(name.strip().lstrip("/"))

    def list(self) -> list[SlashCommand]:
        return [self._commands[k] for k in sorted(self._commands)]

    def load_workflows_dir(self, directory: str) -> int:
        defs = load_workflows(directory)
        for name, wdef in defs.items():
            self.register(
                SlashCommand(
                    name=name,
                    description=wdef.description or f"workflow {name}",
                    kind="workflow",
                    workflow=wdef,
                )
            )
        return len(defs)

    def parse(self, text: str) -> tuple[SlashCommand | None, str]:
        raw = (text or "").strip()
        if not raw.startswith("/"):
            return None, text
        parts = raw[1:].split(maxsplit=1)
        if not parts:
            return None, text
        cmd = self.get(parts[0])
        args = parts[1] if len(parts) > 1 else ""
        return cmd, args
