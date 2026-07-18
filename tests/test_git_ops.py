from __future__ import annotations

import json
import subprocess
from pathlib import Path

from skills import GitOpsSkill


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")
    return repo


def test_git_ops_readonly(tmp_path):
    repo = _init_repo(tmp_path)
    skill = GitOpsSkill(root=repo, allow_write=False)

    status = skill.run(action="status")
    assert status.ok, status.output
    assert json.loads(status.output)["exit_code"] == 0

    log = skill.run(action="log", max_count=5)
    assert log.ok
    assert "init" in json.loads(log.output)["stdout"]

    branch = skill.run(action="branch")
    assert branch.ok

    blocked = skill.run(action="commit", message="nope")
    assert not blocked.ok
    assert "禁止" in blocked.output or "allow_write" in blocked.output


def test_git_ops_add_commit(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "b.txt").write_text("world\n", encoding="utf-8")
    skill = GitOpsSkill(root=repo, allow_write=True)

    added = skill.run(action="add", paths=["b.txt"])
    assert added.ok, added.output

    committed = skill.run(action="commit", message="add b")
    assert committed.ok, committed.output

    log = skill.run(action="log", max_count=2)
    assert "add b" in json.loads(log.output)["stdout"]
