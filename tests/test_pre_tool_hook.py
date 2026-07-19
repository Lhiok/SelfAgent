"""pre_tool 钩子短路权限。"""

from __future__ import annotations

from permission import PermissionDecision, PermissionGuard


def test_pre_tool_hook_can_deny() -> None:
    guard = PermissionGuard(enabled=True, default_effect="allow")

    def block_write(skill: str, action: str | None, _args: dict) -> PermissionDecision | None:
        if skill == "local_file" and action == "write":
            return PermissionDecision.deny("hook blocked write")
        return None

    guard.add_pre_tool_hook(block_write)
    d = guard.check("local_file", action="write", arguments={"action": "write"})
    assert not d.allowed
    assert "hook blocked" in d.reason

    d2 = guard.check("local_file", action="read", arguments={"action": "read"})
    assert d2.allowed
