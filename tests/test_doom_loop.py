"""doom-loop 检测。"""

from session.doom_loop import DoomLoopTracker
from session.types import ActionCall, AgentStep


def _step(action: str, inp: str, *, ok: bool = True) -> AgentStep:
    return AgentStep(
        index=1,
        thought="t",
        calls=[
            ActionCall(
                action=action,
                action_input=inp,
                observation="x",
                ok=ok,
            )
        ],
    )


def test_repeat_warn_then_stop():
    tracker = DoomLoopTracker(window=6, repeat_limit=3, fail_limit=10, warn_once=True)
    assert not tracker.observe(_step("local_file", '{"a":1}')).triggered
    assert not tracker.observe(_step("local_file", '{"a":1}')).triggered
    v = tracker.observe(_step("local_file", '{"a":1}'))
    assert v.triggered and v.inject
    v2 = tracker.observe(_step("local_file", '{"a":1}'))
    assert v2.triggered and v2.message and not v2.inject


def test_fail_streak_stops():
    tracker = DoomLoopTracker(window=6, repeat_limit=10, fail_limit=3)
    assert not tracker.observe(_step("shell_run", "{}", ok=False)).triggered
    assert not tracker.observe(_step("shell_run", "{}", ok=False)).triggered
    v = tracker.observe(_step("shell_run", "{}", ok=False))
    assert v.triggered and v.message
