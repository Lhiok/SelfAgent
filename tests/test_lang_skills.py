from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from skills import CsharpSkill, NodejsSkill, PythonSkill, SkillRegistry
from skills.lang_runtime import resolve_cwd


def test_resolve_cwd_blocks_escape(tmp_path: Path):
    cwd, err = resolve_cwd(tmp_path, "../outside")
    assert cwd is None
    assert err is not None


def test_python_run_and_compile(tmp_path: Path):
    (tmp_path / "hello.py").write_text("print(42)\n", encoding="utf-8")
    skill = PythonSkill(root=tmp_path)
    run = skill.run(action="run", file="hello.py")
    assert run.ok, run.output
    assert "42" in run.output

    bad = skill.run(action="compile", file="../x.py")
    assert not bad.ok

    compiled = skill.run(action="compile", file="hello.py")
    assert compiled.ok, compiled.output

    ver = skill.run(action="version")
    assert ver.ok
    assert "Python" in ver.output or "python" in ver.output.lower()


def test_python_install_rejects_injection(tmp_path: Path):
    skill = PythonSkill(root=tmp_path)
    result = skill.run(action="install", packages=["ok", "evil;rm"])
    assert not result.ok
    assert "非法" in result.output


def test_nodejs_missing_file(tmp_path: Path):
    skill = NodejsSkill(root=tmp_path)
    with patch("skills.nodejs.which_bin", return_value="node"):
        result = skill.run(action="run", file="missing.js")
    assert not result.ok
    assert "不存在" in result.output


def test_nodejs_version_without_node(tmp_path: Path):
    skill = NodejsSkill(root=tmp_path)

    def no_bins(*names: str):
        return None

    with patch("skills.nodejs.which_bin", side_effect=no_bins):
        result = skill.run(action="version")
    assert not result.ok
    assert "未安装" in result.output


def test_csharp_missing_dotnet(tmp_path: Path):
    skill = CsharpSkill(root=tmp_path, dotnet_bin="dotnet_missing_xyz")
    with patch("skills.csharp.which_bin", return_value=None):
        result = skill.run(action="build")
    assert not result.ok
    assert "未找到" in result.output


def test_registry_registers_lang_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "skills.registry.cfg.get_section",
        lambda name, default=None: (
            {
                "local_file": {"root": str(tmp_path)},
                "ask_user": {"enabled": False},
                "request_capability": {"enabled": False},
                "search_code": {"enabled": False},
                "shell_run": {"enabled": False},
                "feishu_notify": {"enabled": False},
                "git_ops": {"enabled": False},
                "dotnet_build": {"enabled": False},
                "nodejs": {"enabled": True},
                "python": {"enabled": True},
                "csharp": {"enabled": True},
                "web_fetch": {"enabled": False},
                "http_request": {"enabled": False},
                "diff_review": {"enabled": False},
                "todo_tracker": {"enabled": False},
                "screenshot": {"enabled": False},
                "browser": {"enabled": False},
            }
            if name == "skills"
            else (default or {})
        ),
    )
    reg = SkillRegistry.from_config(load_permission=False, workdir=tmp_path)
    assert reg.get("nodejs") is not None
    assert reg.get("python") is not None
    assert reg.get("csharp") is not None
