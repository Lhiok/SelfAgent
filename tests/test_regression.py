from __future__ import annotations

from regression import run_regression


def test_offline_regression_suite_passes():
    report = run_regression(include_live=False)
    assert report.ok, report.summary()
    assert report.passed >= 10
    assert report.failed == 0


def test_regression_module_filter():
    report = run_regression(include_live=False, modules=["env", "log"])
    assert report.ok
    assert all(r.module in {"env", "log"} for r in report.results)
    assert report.total >= 4
