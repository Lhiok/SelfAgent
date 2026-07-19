from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from session import (
    DETAIL_FULL,
    DETAIL_OFF,
    DETAIL_SUMMARY,
    ActionCall,
    Agent,
    AgentResult,
    AgentStep,
    format_result_detail,
    format_step_detail,
    parse_detail_level,
)
from skills import LocalFileSkill, SkillRegistry


def test_parse_detail_level_aliases():
    assert parse_detail_level("off") == DETAIL_OFF
    assert parse_detail_level("summary") == DETAIL_SUMMARY
    assert parse_detail_level("full") == DETAIL_FULL
    assert parse_detail_level("true") == DETAIL_SUMMARY
    assert parse_detail_level("false") == DETAIL_OFF


def test_format_step_levels():
    step = AgentStep(
        index=1,
        thought="先列目录",
        calls=[
            ActionCall(
                action="local_file",
                action_input='{"action":"list","path":"."}',
                observation='{"entries":["a.py"]}',
                ok=True,
            )
        ],
    )
    summary = format_step_detail(step, "summary")
    assert "Thought: 先列目录" in summary
    assert "Action: local_file (ok)" in summary
    assert "Observation:" not in summary

    full = format_step_detail(step, "full")
    assert "Action Input:" in full
    assert "Observation:" in full
    assert format_step_detail(step, "off") == ""


def test_agent_streams_detail(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp, tc

    chunks: list[str] = []
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    tc("local_file", {"action": "list", "path": "."}, id="list1"),
                    content="列一下",
                ),
                resp(finish("只有空目录"), content="好了"),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        detail="full",
        stream_detail=True,
        on_detail=chunks.append,
        max_steps=5,
    )
    result = agent.run("列出目录")
    assert result.completed
    assert result.detail_level == "full"
    assert result.detail_text
    assert any("Action: local_file" in c for c in chunks)
    assert any("Final Answer" in c for c in chunks)


def test_agent_progress_even_when_detail_off(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp, tc

    events: list[dict] = []
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(tc("local_file", {"action": "list", "path": "."}, id="list1")),
                resp(finish("ok")),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        detail="off",
        stream_detail=True,
        on_progress=events.append,
        max_steps=5,
    )
    result = agent.run("列出目录")
    assert result.completed
    assert any(e.get("type") == "status" and e.get("phase") == "thinking" for e in events)
    assert any(e.get("type") == "status" and e.get("phase") == "acting" for e in events)
    steps = [e for e in events if e.get("type") == "step"]
    assert len(steps) >= 2
    assert steps[0]["actions"][0]["action"] == "local_file"


def test_agent_detail_off_silent(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp

    chunks: list[str] = []
    agent = Agent(
        ai=ScriptedAI([resp(finish("done"))]),
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        detail="off",
        on_detail=chunks.append,
    )
    result = agent.run("hi")
    assert result.answer == "done"
    assert result.detail_text == ""
    assert chunks == []


def test_agent_detail_persists_to_run_log(tmp_path):
    import config as cfg
    from log import Logger, reset_logger
    from scripted_ai import ScriptedAI, finish, resp

    cfg.set_config(
        {
            "log": {
                "level": "notice",
                "modes": ["file"],
                "file": {"path": str(tmp_path / "app.log")},
            }
        }
    )
    reset_logger()

    # 触发 from_config 绑定本次运行日志
    Logger.from_config("react")
    agent = Agent(
        ai=ScriptedAI([resp(finish("完成"), content="思考中")]),
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        detail="summary",
        stream_detail=False,  # 不打印控制台，仍应落盘
        max_steps=3,
    )
    result = agent.run("任务")
    assert result.completed
    from log import get_run_log_path

    path = get_run_log_path()
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "Agent 细节" in text
    assert "思考中" in text
    assert "Final Answer: 完成" in text


def test_format_result_and_set_detail():
    result = AgentResult(
        answer="ok",
        steps=[
            AgentStep(index=1, thought="t", final_answer="ok"),
        ],
        detail_level="summary",
    )
    text = format_result_detail(result, "summary")
    assert "第 1 步" in text
    assert "Final Answer: ok" in result.format_detail("summary")

    agent = Agent(
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        detail="off",
    )
    agent.set_detail("summary")
    assert agent.detail == "summary"
    cloned = agent.with_mode("plan")
    assert cloned.detail == "summary"
