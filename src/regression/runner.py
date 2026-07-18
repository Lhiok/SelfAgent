"""回归执行器。"""

from __future__ import annotations

import time
import traceback
from typing import Iterable

import config as cfg
from log import get_logger
from regression.cases import SkipCase, build_cases, filter_cases
from regression.models import CaseResult, SuiteReport

logger = get_logger("regression")


class RegressionRunner:
    def __init__(
        self,
        *,
        include_live: bool | None = None,
        modules: Iterable[str] | None = None,
    ) -> None:
        section = cfg.get_section("regression", {}) or {}
        if include_live is None:
            include_live = bool(section.get("include_live", False))
        self.include_live = include_live
        if modules is None:
            configured = section.get("modules")
            modules = list(configured) if configured else None
        self.modules = list(modules) if modules else None

    def run(self) -> SuiteReport:
        cases = filter_cases(
            build_cases(include_live=self.include_live),
            modules=self.modules,
        )
        report = SuiteReport()
        logger.notice(
            f"开始回归：用例 {len(cases)}，live={self.include_live}，"
            f"modules={self.modules or 'all'}"
        )

        for module, name, fn in cases:
            started = time.perf_counter()
            try:
                fn()
                status = "passed"
                message = ""
            except SkipCase as exc:
                status = "skipped"
                message = str(exc)
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                message = f"{exc}\n{traceback.format_exc(limit=3)}"
            duration_ms = (time.perf_counter() - started) * 1000
            result = CaseResult(
                name=name,
                module=module,
                status=status,  # type: ignore[arg-type]
                message=message.strip(),
                duration_ms=duration_ms,
            )
            report.results.append(result)
            log_fn = {
                "passed": logger.notice,
                "failed": logger.critical,
                "skipped": logger.warning,
            }[status]
            log_fn(f"[{status.upper()}] [{module}] {name} ({duration_ms:.1f}ms)")

        logger.notice(
            f"回归结束：通过 {report.passed} / 失败 {report.failed} / 跳过 {report.skipped}"
        )
        return report


def run_regression(
    *,
    include_live: bool | None = None,
    modules: Iterable[str] | None = None,
) -> SuiteReport:
    return RegressionRunner(include_live=include_live, modules=modules).run()
