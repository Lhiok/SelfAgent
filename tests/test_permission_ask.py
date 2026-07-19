"""交互式 permission ask（canUseTool）。"""

from __future__ import annotations

from pathlib import Path

from permission import PermissionGuard
from skills import LocalFileSkill, SkillRegistry
from skills.bridge import SkillBridge


def test_ask_handler_allow_then_write(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    guard = PermissionGuard(
        enabled=True,
        default_effect="deny",
        allow=["local_file(list)", "local_file(read)"],
        ask=["local_file(write)"],
    )
    calls: list[dict] = []

    def handler(payload: dict) -> bool:
        calls.append(payload)
        return True

    guard.ask_handler = handler
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=root, allow_write=True))
    reg.set_permission(guard)
    bridge = SkillBridge(reg)

    result = bridge.invoke(
        "local_file",
        {"action": "write", "path": "a.txt", "content": "ok"},
        permission=guard,
    )
    assert result.ok, result.output
    assert (root / "a.txt").read_text(encoding="utf-8") == "ok"
    assert len(calls) == 1
    assert calls[0]["skill"] == "local_file"
    # grant 后再次写入不再 ask
    calls.clear()
    result2 = bridge.invoke(
        "local_file",
        {"action": "write", "path": "b.txt", "content": "x"},
        permission=guard,
    )
    assert result2.ok, result2.output
    assert calls == []


def test_ask_handler_deny(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    guard = PermissionGuard(
        enabled=True,
        default_effect="deny",
        ask=["local_file(write)"],
    )
    guard.ask_handler = lambda _p: False
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=root, allow_write=True))
    bridge = SkillBridge(reg)
    result = bridge.invoke(
        "local_file",
        {"action": "write", "path": "a.txt", "content": "no"},
        permission=guard,
    )
    assert not result.ok
    assert "权限拒绝" in result.output
    assert not (root / "a.txt").exists()


def test_ask_without_handler_denies(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    guard = PermissionGuard(
        enabled=True,
        default_effect="deny",
        ask=["local_file(write)"],
    )
    reg = SkillRegistry()
    reg.register(LocalFileSkill(root=root, allow_write=True))
    bridge = SkillBridge(reg)
    result = bridge.invoke(
        "local_file",
        {"action": "write", "path": "a.txt", "content": "no"},
        permission=guard,
    )
    assert not result.ok
    assert not (root / "a.txt").exists()
