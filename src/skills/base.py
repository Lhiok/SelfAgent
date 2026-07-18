"""Skill 能力层抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillResult:
    ok: bool
    output: str
    data: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """单个可被 ReAct 调用的能力。"""

    name: str
    description: str
    parameters_schema: dict[str, Any]

    @abstractmethod
    def run(self, **kwargs: Any) -> SkillResult:
        raise NotImplementedError

    def schema_text(self) -> str:
        import json

        return (
            f"- {self.name}: {self.description}\n"
            f"  参数 JSON Schema: {json.dumps(self.parameters_schema, ensure_ascii=False)}"
        )
