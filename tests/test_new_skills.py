from __future__ import annotations

import json
from unittest.mock import MagicMock

from skills import FeishuNotifySkill, SearchCodeSkill, ShellRunSkill


def test_search_code_finds_keyword(tmp_path):
    (tmp_path / "a.py").write_text("def hello_world():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing", encoding="utf-8")
    skill = SearchCodeSkill(root=tmp_path)
    result = skill.run(query="hello_world", extensions=["py"])
    assert result.ok
    data = json.loads(result.output)
    assert data["count"] >= 1
    assert data["hits"][0]["path"] == "a.py"


def test_shell_run_allowlist_and_python(tmp_path):
    import sys
    from pathlib import Path

    exe = sys.executable
    allow = ["python", "python3", "py", Path(exe).name.lower().replace(".exe", "")]
    skill = ShellRunSkill(root=tmp_path, allow_commands=allow)
    ok = skill.run(argv=[exe, "-c", "print(12345)"])
    assert ok.ok, ok.output
    data = json.loads(ok.output)
    assert data["exit_code"] == 0
    assert "12345" in (data["stdout"] + data["stderr"])

    denied = skill.run(command="rm -rf /")
    assert not denied.ok


def test_shell_run_blocks_pipe(tmp_path):
    skill = ShellRunSkill(root=tmp_path, allow_commands=["python", "python3", "py"])
    result = skill.run(command='python -c "print(1)" | more')
    assert not result.ok
    assert "禁止" in result.output or "|" in result.output


def test_feishu_notify_uses_bot():
    bot = MagicMock()
    bot.send_text.return_value = MagicMock(ok=True, code=0, msg="ok", raw={})
    bot.send_markdown.return_value = MagicMock(ok=True, code=0, msg="ok", raw={})
    skill = FeishuNotifySkill(bot=bot)

    text_res = skill.run(action="text", text="hi")
    assert text_res.ok
    bot.send_text.assert_called_once_with("hi")

    md_res = skill.run(action="markdown", title="T", content="body")
    assert md_res.ok
    bot.send_markdown.assert_called_once_with("T", "body")
