"""回归结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CaseStatus = Literal["passed", "failed", "skipped"]


@dataclass
class CaseResult:
    name: str
    module: str
    status: CaseStatus
    message: str = ""
    duration_ms: float = 0.0


@dataclass
class SuiteReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == "skipped")

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        lines = [
            "===== SelfAgent 回归报告 =====",
            f"总计 {self.total} | 通过 {self.passed} | 失败 {self.failed} | 跳过 {self.skipped}",
            "",
        ]
        for r in self.results:
            mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[r.status]
            detail = f" — {r.message}" if r.message else ""
            lines.append(
                f"[{mark}] [{r.module}] {r.name} ({r.duration_ms:.1f}ms){detail}"
            )
        lines.append("")
        lines.append("结果: " + ("全部通过" if self.ok else "存在失败"))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "results": [
                {
                    "name": r.name,
                    "module": r.module,
                    "status": r.status,
                    "message": r.message,
                    "duration_ms": r.duration_ms,
                }
                for r in self.results
            ],
        }
