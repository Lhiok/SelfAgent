from __future__ import annotations

from ai.base import AIClient, AIResponse, ChatOptions
from permission import PermissionGuard
from session import (
    ActionCall,
    Agent,
    AgentStep,
    build_unified_diff,
    collect_changes_from_steps,
    extract_change_from_call,
)
from skills import LocalFileSkill, SkillRegistry


def test_build_unified_diff_shows_added_line():
    diff = build_unified_diff("a.py", "x = 1\n", "x = 1\ny = 2\n")
    assert "a/a.py" in diff
    assert "+y = 2" in diff


def test_extract_change_from_action_input_write():
    change = extract_change_from_call(
        skill_name="local_file",
        action_input='{"action":"write","path":"hi.py","content":"print(1)\\n"}',
        ok=True,
    )
    assert change is not None
    assert change["kind"] == "write"
    assert change["path"] == "hi.py"
    assert "print(1)" in change["new_text"]
    assert "+print(1)" in change["diff"]


def test_local_file_write_includes_change_data(tmp_path):
    skill = LocalFileSkill(root=tmp_path, allow_write=True)
    result = skill.run(action="write", path="demo.py", content="hello\n")
    assert result.ok
    assert result.data["change"]["path"] == "demo.py"
    assert result.data["change"]["new_text"] == "hello\n"

    change = extract_change_from_call(
        skill_name="local_file",
        action_input='{"action":"write","path":"demo.py"}',
        data=result.data,
        ok=True,
    )
    assert change is not None
    assert "+hello" in change["diff"]


def test_local_file_patch_full_file_diff(tmp_path):
    target = tmp_path / "x.py"
    target.write_text("a = 1\nb = 2\n", encoding="utf-8")
    skill = LocalFileSkill(root=tmp_path, allow_write=True)
    result = skill.run(
        action="patch",
        path="x.py",
        old_text="b = 2",
        new_text="b = 3",
    )
    assert result.ok
    change = extract_change_from_call(
        skill_name="local_file",
        action_input="{}",
        data=result.data,
        ok=True,
    )
    assert change["kind"] == "patch"
    assert "-b = 2" in change["diff"]
    assert "+b = 3" in change["diff"]


def test_collect_changes_from_steps():
    steps = [
        AgentStep(
            index=1,
            thought="改一下",
            calls=[
                ActionCall(
                    action="local_file",
                    action_input='{"action":"write","path":"n.py","content":"1\\n"}',
                    observation="ok",
                    ok=True,
                    data={
                        "change": {
                            "kind": "write",
                            "path": "n.py",
                            "old_text": "",
                            "new_text": "1\n",
                        }
                    },
                )
            ],
        )
    ]
    changes = collect_changes_from_steps(steps)
    assert len(changes) == 1
    assert changes[0]["path"] == "n.py"


def test_merge_same_file_patches():
    from session import merge_changes_by_path

    steps = [
        AgentStep(
            index=1,
            thought="改两处",
            calls=[
                ActionCall(
                    action="local_file",
                    action_input="{}",
                    observation="ok",
                    ok=True,
                    data={
                        "change": {
                            "kind": "patch",
                            "path": "Assets/A.cs",
                            "old_text": "void Foo() {}",
                            "new_text": "void Foo() { return; }",
                        }
                    },
                ),
                ActionCall(
                    action="local_file",
                    action_input="{}",
                    observation="ok",
                    ok=True,
                    data={
                        "change": {
                            "kind": "patch",
                            "path": "Assets/A.cs",
                            "old_text": "void Bar() {}",
                            "new_text": "void Bar() { return; }",
                        }
                    },
                ),
            ],
        )
    ]
    changes = collect_changes_from_steps(steps)
    assert len(changes) == 1
    assert changes[0]["path"] == "Assets/A.cs"
    assert changes[0]["patch_count"] == 2
    assert "Foo" in changes[0]["diff"]
    assert "Bar" in changes[0]["diff"]

    merged = merge_changes_by_path(
        [
            {"kind": "patch", "path": "x.py", "old_text": "a", "new_text": "b"},
            {"kind": "patch", "path": "x.py", "old_text": "c", "new_text": "d"},
            {"kind": "patch", "path": "y.py", "old_text": "e", "new_text": "f"},
        ]
    )
    assert [c["path"] for c in merged] == ["x.py", "y.py"]
    assert merged[0]["patch_count"] == 2


def test_agent_progress_includes_file_changes(tmp_path):
    from scripted_ai import ScriptedAI, finish, resp, tc

    events: list[dict] = []
    reg = SkillRegistry(permission=PermissionGuard.allow_all())
    reg.register(LocalFileSkill(root=tmp_path, allow_write=True))
    agent = Agent(
        ai=ScriptedAI(
            [
                resp(
                    tc(
                        "local_file",
                        {"action": "write", "path": "out.py", "content": "ok\n"},
                        id="w1",
                    )
                ),
                resp(finish("已写入")),
            ]
        ),
        skills=reg,
        permission=PermissionGuard.allow_all(),
        detail="off",
        on_progress=events.append,
        max_steps=5,
        workdir=tmp_path,
    )
    result = agent.run("写一个文件")
    assert result.completed
    step_events = [e for e in events if e.get("type") == "step" and e.get("changes")]
    assert step_events
    assert step_events[0]["changes"][0]["path"] == "out.py"
    assert "+ok" in step_events[0]["changes"][0]["diff"]
