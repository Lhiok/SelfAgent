"""上下文压缩。"""

from ai import AIMessage
from react.compaction import compact_messages, compaction_settings, hard_trim


def test_hard_trim_keeps_recent():
    msgs = [AIMessage(role="user", content=f"u{i}") for i in range(10)]
    out = hard_trim(msgs, keep_recent=3)
    assert len(out) == 3
    assert out[0].content == "u7"


def test_compact_without_ai_falls_back():
    msgs = [AIMessage(role="user", content=f"msg-{i}") for i in range(20)]
    out = compact_messages(msgs, ai=None, compact_after=8, keep_recent=4)
    assert any("[上下文摘要]" in (m.content or "") for m in out)
    assert len(out) == 5  # summary + 4 recent


def test_compaction_settings_default_skips_ai():
    after, keep, use_ai = compaction_settings({})
    assert after == 0
    assert keep == 12
    assert use_ai is False


def test_compaction_settings_opt_in_ai():
    after, keep, use_ai = compaction_settings(
        {"compact_after_messages": 32, "compact_keep_recent": 8, "compact_use_ai": True}
    )
    assert after == 32
    assert keep == 8
    assert use_ai is True


class _SlowAskAI:
    provider = "fake"

    def __init__(self) -> None:
        self.ask_calls = 0

    def ask(self, prompt: str) -> str:
        self.ask_calls += 1
        return "摘要"


def test_hot_path_trim_skips_ai_by_default(tmp_path):
    from permission import PermissionGuard
    from react import Conversation, ReActAgent
    from skills import SkillRegistry

    ai = _SlowAskAI()
    agent = ReActAgent(
        ai=ai,
        skills=SkillRegistry(permission=PermissionGuard.allow_all()),
        permission=PermissionGuard.allow_all(),
        stream_ai=False,
    )
    # 注入假 ai.ask 供 compact 使用
    agent.ai = ai  # type: ignore[assignment]
    conv = Conversation(
        agent=agent,
        persist_dir=tmp_path,
        max_history_messages=100,
    )
    conv.compact_after_messages = 8
    conv.compact_keep_recent = 4
    conv.compact_use_ai = False
    conv.messages = [AIMessage(role="user", content=f"m{i}") for i in range(20)]
    conv._trim_history()
    assert ai.ask_calls == 0
    assert len(conv.messages) <= 5
