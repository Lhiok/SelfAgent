from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from skills import RequestCapabilitySkill


def test_request_capability_writes_doc_and_notifies(tmp_path):
    bot = MagicMock()
    bot.send_markdown.return_value = MagicMock(ok=True, code=0, msg="ok", raw={})
    out = tmp_path / "reqs"
    skill = RequestCapabilitySkill(output_dir=out, bot=bot)

    result = skill.run(
        title="需要浏览器自动化",
        need="打开网页并截图",
        why="现有 shell_run / local_file 无法操控浏览器",
        context="任务：验证登录页",
        workaround="先用文档说明手工步骤",
        priority="high",
    )
    assert result.ok, result.output
    data = json.loads(result.output)
    assert data["priority"] == "high"
    assert data["notified"] is True
    assert data["reminder"]

    path = Path(data["path"])
    assert path.exists()
    assert path.parent == out.resolve()
    text = path.read_text(encoding="utf-8")
    assert "# 需要浏览器自动化" in text
    assert "打开网页并截图" in text
    assert "无法操控浏览器" in text
    assert "待评审" in text

    bot.send_markdown.assert_called_once()
    title, content = bot.send_markdown.call_args.args
    assert "能力需求" in title
    assert "需要浏览器自动化" in content
    assert str(path) in content


def test_request_capability_skips_notify_when_disabled(tmp_path):
    bot = MagicMock()
    skill = RequestCapabilitySkill(
        output_dir=tmp_path / "reqs",
        notify_feishu=False,
        bot=bot,
    )
    result = skill.run(
        title="需要邮件发送",
        need="SMTP 发信",
        why="无邮件 Skill",
        notify=True,  # 配置关闭时调用方无法强制开启
    )
    assert result.ok
    data = json.loads(result.output)
    assert data["notified"] is None
    bot.send_markdown.assert_not_called()


def test_request_capability_respects_notify_false(tmp_path):
    bot = MagicMock()
    skill = RequestCapabilitySkill(output_dir=tmp_path / "reqs", bot=bot)
    result = skill.run(
        title="需要 OCR",
        need="识别图片文字",
        why="无视觉 Skill",
        notify=False,
    )
    assert result.ok
    data = json.loads(result.output)
    assert data["notified"] is None
    bot.send_markdown.assert_not_called()
    assert list((tmp_path / "reqs").glob("*.md"))


def test_request_capability_requires_fields(tmp_path):
    skill = RequestCapabilitySkill(output_dir=tmp_path)
    assert not skill.run(title="", need="x", why="y").ok
    assert not skill.run(title="t", need="", why="y").ok
    assert not skill.run(title="t", need="n", why="").ok
    bad = skill.run(title="t", need="n", why="w", priority="urgent")
    assert not bad.ok


def test_request_capability_file_ok_when_feishu_fails(tmp_path):
    bot = MagicMock()
    bot.send_markdown.return_value = MagicMock(
        ok=False, code=19001, msg="webhook missing", raw={}
    )
    skill = RequestCapabilitySkill(output_dir=tmp_path / "reqs", bot=bot)
    result = skill.run(title="需要日历", need="读日程", why="无日历 Skill")
    assert result.ok
    data = json.loads(result.output)
    assert data["notified"] is False
    assert Path(data["path"]).exists()
