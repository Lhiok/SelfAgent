from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from react import (
    DETAIL_FULL,
    DETAIL_OFF,
    DETAIL_SUMMARY,
    ActionCall,
    ReActAgent,
    ReActResult,
    ReActStep,
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
    step = ReActStep(
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
    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: 列一下\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"list","path":"."}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content="Thought: 好了\nFinal Answer: 只有空目录\n",
                model="s",
                provider=self.provider,
            )

    chunks: list[str] = []
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=False))
    agent = ReActAgent(
        ai=_AI(),
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


def test_agent_detail_off_silent(tmp_path):
    class _AI(AIClient):
        provider = "scripted"

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            return AIResponse(
                content="Thought: x\nFinal Answer: done\n",
                model="s",
                provider=self.provider,
            )

    chunks: list[str] = []
    agent = ReActAgent(
        ai=_AI(),
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

    class _AI(AIClient):
        provider = "scripted"

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            return AIResponse(
                content="Thought: 思考中\nFinal Answer: 完成\n",
                model="s",
                provider=self.provider,
            )

    # 触发 from_config 绑定本次运行日志
    Logger.from_config("react")
    agent = ReActAgent(
        ai=_AI(),
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
    assert "ReAct 细节" in text
    assert "Thought: 思考中" in text
    assert "Final Answer: 完成" in text


def test_format_result_and_set_detail():
    result = ReActResult(
        answer="ok",
        steps=[
            ReActStep(index=1, thought="t", final_answer="ok"),
        ],
        detail_level="summary",
    )
    text = format_result_detail(result, "summary")
    assert "第 1 步" in text
    assert "Final Answer: ok" in result.format_detail("summary")

    agent = ReActAgent(
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        detail="off",
    )
    agent.set_detail("summary")
    assert agent.detail == "summary"
    cloned = agent.with_mode("plan")
    assert cloned.detail == "summary"
