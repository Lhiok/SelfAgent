"""workflow.runner 线性 / depends_on / 失败 stop。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from permission import PermissionGuard
from skills.base import Skill, SkillResult
from skills import SkillBridge, SkillRegistry
from workflow.loader import load_workflows
from workflow.runner import WorkflowRunner
from workflow.types import RunStatus, StepStatus, WorkflowDef, WorkflowStep


class _Echo(Skill):
    name = "echo"
    description = "echo"
    parameters_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }

    def run(self, **kwargs: Any) -> SkillResult:
        return SkillResult(ok=True, output=str(kwargs.get("text") or ""))


class _Fail(Skill):
    name = "fail"
    description = "fail"
    parameters_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> SkillResult:
        return SkillResult(ok=False, output="boom")


def _bridge(*skills: Skill) -> SkillBridge:
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return SkillBridge(reg)


def test_linear_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "workflows" / "demo.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text(
        """
name: demo
steps:
  - id: a
    skill: echo
    input: { text: "one" }
  - id: b
    skill: echo
    input: { text: "two" }
    depends_on: [a]
""",
        encoding="utf-8",
    )
    defs = load_workflows(yaml_path.parent)
    assert "demo" in defs
    runner = WorkflowRunner(
        _bridge(_Echo()),
        PermissionGuard(enabled=False),
        runs_base=tmp_path / "wf",
    )
    run = runner.from_def(defs["demo"])
    finished = runner.run_sync(run)
    assert finished.status == RunStatus.DONE
    assert [s.output for s in finished.steps] == ["one", "two"]


def test_depends_on_order(tmp_path: Path) -> None:
    wdef = WorkflowDef(
        name="dag",
        steps=[
            WorkflowStep(id="b", skill="echo", input={"text": "B"}, depends_on=["a"]),
            WorkflowStep(id="a", skill="echo", input={"text": "A"}),
        ],
    )
    runner = WorkflowRunner(
        _bridge(_Echo()),
        PermissionGuard(enabled=False),
        runs_base=tmp_path / "wf",
    )
    finished = runner.run_sync(runner.from_def(wdef))
    assert finished.status == RunStatus.DONE
    assert finished.steps[0].id == "b"
    assert finished.steps[0].status == StepStatus.DONE
    by = {s.id: s for s in finished.steps}
    assert by["a"].output == "A"
    assert by["b"].output == "B"


def test_fail_stop(tmp_path: Path) -> None:
    wdef = WorkflowDef(
        name="x",
        on_error="stop",
        steps=[
            WorkflowStep(id="a", skill="fail"),
            WorkflowStep(id="b", skill="echo", input={"text": "x"}, depends_on=["a"]),
        ],
    )
    runner = WorkflowRunner(
        _bridge(_Echo(), _Fail()),
        PermissionGuard(enabled=False),
        runs_base=tmp_path / "wf",
    )
    finished = runner.run_sync(runner.from_def(wdef))
    assert finished.status == RunStatus.FAILED
    by = {s.id: s for s in finished.steps}
    assert by["a"].status == StepStatus.FAILED
    assert by["b"].status == StepStatus.SKIPPED
