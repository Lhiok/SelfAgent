from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from skills import (
    DiffReviewSkill,
    DotnetBuildSkill,
    HttpRequestSkill,
    ScreenshotSkill,
    SkillRegistry,
    TodoTrackerSkill,
    WebFetchSkill,
)
from skills.http_util import host_is_private, validate_http_url


def test_http_util_blocks_private():
    assert host_is_private("127.0.0.1")
    assert host_is_private("localhost")
    assert host_is_private("10.0.0.1")
    assert not host_is_private("8.8.8.8")
    assert validate_http_url("http://127.0.0.1/") is not None
    assert validate_http_url("https://8.8.8.8/") is None


def test_todo_tracker_lifecycle(tmp_path: Path):
    skill = TodoTrackerSkill(root=tmp_path)
    added = skill.run(action="add", items=["a", "b"])
    assert added.ok
    items = added.data["items"]
    assert len(items) == 2
    tid = items[0]["id"]
    done = skill.run(action="complete", id=tid)
    assert done.ok
    assert any(it["id"] == tid and it["status"] == "done" for it in done.data["items"])
    listed = skill.run(action="list")
    assert "任务清单" in listed.output
    assert (tmp_path / ".selfagent" / "todos.json").is_file()


def test_diff_review_text_risks():
    skill = DiffReviewSkill(root=".")
    diff = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "+password = 'secret'\n"
        "+print(1)\n"
    )
    result = skill.run(action="review", source="text", diff=diff)
    assert result.ok
    assert result.data["added"] >= 2
    assert any(r["label"] == "疑似硬编码密码" for r in result.data["risks"])


def test_screenshot_read_png(tmp_path: Path):
    # 最小合法 PNG 1x1
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
    path = tmp_path / "t.png"
    path.write_bytes(png)
    skill = ScreenshotSkill(root=tmp_path)
    result = skill.run(action="read", path="t.png")
    assert result.ok
    assert result.data["width"] == 1
    assert result.data["height"] == 1


def test_dotnet_build_missing_cli(tmp_path: Path):
    skill = DotnetBuildSkill(root=tmp_path, dotnet_bin="dotnet_not_exist_xyz")
    with patch("shutil.which", return_value=None):
        result = skill.run(action="build")
    assert not result.ok
    assert "未找到" in result.output


def test_web_fetch_html(monkeypatch: pytest.MonkeyPatch):
    skill = WebFetchSkill(allow_private=True)

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body><h1>Hi</h1><script>x</script></body></html>"
        url = "http://127.0.0.1/doc"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = skill.run(url="http://127.0.0.1/doc")
    assert result.ok
    assert "Hi" in result.output
    assert "script" not in result.output.lower() or "x" not in result.output


def test_http_request_get(monkeypatch: pytest.MonkeyPatch):
    skill = HttpRequestSkill(allow_private=True)

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'
        url = "http://127.0.0.1/api"

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kwargs):
            assert method == "GET"
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    result = skill.run(action="get", url="http://127.0.0.1/api")
    assert result.ok
    assert result.data["status_code"] == 200


def test_registry_registers_new_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
                "dotnet_build": {"enabled": True},
                "web_fetch": {"enabled": True},
                "http_request": {"enabled": True},
                "diff_review": {"enabled": True},
                "todo_tracker": {"enabled": True},
                "screenshot": {"enabled": True},
                "browser": {"enabled": True},
            }
            if name == "skills"
            else (default or {})
        ),
    )
    reg = SkillRegistry.from_config(load_permission=False, workdir=tmp_path)
    for name in (
        "dotnet_build",
        "web_fetch",
        "http_request",
        "diff_review",
        "todo_tracker",
        "screenshot",
        "browser",
    ):
        assert reg.get(name) is not None, name


def test_browser_missing_playwright():
    from skills.browser import BrowserSkill

    skill = BrowserSkill(root=".")
    # 强制走 ImportError 路径
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        result = skill.run(action="goto", url="https://example.com")
    assert not result.ok
    assert "playwright" in result.output.lower()
