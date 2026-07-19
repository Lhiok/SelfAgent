"""SkillBridge 截断、JSON 围栏与进度。"""

from permission import PermissionGuard
from session.control import RunControl
from skills import SkillBridge, SkillRegistry
from skills.base import Skill, SkillResult
from skills.bridge import parse_arguments


class _Echo(Skill):
    name = "echo"
    description = "echo"
    parameters_schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    def run(self, **kwargs):
        return SkillResult(ok=True, output="x" * 100, data={})


def test_parse_fenced_json():
    args, err = parse_arguments('```json\n{"text":"hi"}\n```')
    assert err is None
    assert args == {"text": "hi"}


def test_truncate_output():
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(_Echo())
    bridge = SkillBridge(reg, output_max_chars=20, output_max_bytes=None)
    result = bridge.invoke("echo", {"text": "a"})
    assert result.ok
    assert result.data.get("truncated") is True
    assert len(result.output) <= 20


def test_progress_events():
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(_Echo())
    bridge = SkillBridge(reg, output_max_chars=1000)
    events: list[dict] = []
    bridge.invoke("echo", {}, on_progress=events.append)
    assert events[0]["status"] == "start"
    assert events[-1]["status"] == "end"


def test_shell_cancel(tmp_path):
    from skills.shell_run import ShellRunSkill

    skill = ShellRunSkill(root=tmp_path, allow_commands=["python", "python3", "py"])
    control = RunControl()
    control.begin_run()
    control.cancel()
    # 用长时间 sleep；若 cancel 生效应很快返回
    result = skill.run(
        argv=["python", "-c", "import time\ntime.sleep(30)"],
        timeout=60,
        _control=control,
    )
    assert result.ok is False
    assert "取消" in result.output
