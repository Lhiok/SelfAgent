"""各模块回归用例（默认离线可跑；live 用例需密钥）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

import config as cfg
from ai.base import AIClient, AIResponse, ChatOptions
from ai.factory import create_ai_client
from env import EnvLayer, get_env
from feishu import FeishuBot
from log import LogLevel, Logger
from permission import PermissionGuard
from permission.guard import RolePolicy, SkillRule
from react import AgentMode, Conversation, Plan, PlanStep, ReActAgent, parse_plan
from react.parser import parse_react_output
from skills import (
    AskUserSkill,
    FeishuNotifySkill,
    GitOpsSkill,
    LocalFileSkill,
    RequestCapabilitySkill,
    SearchCodeSkill,
    ShellRunSkill,
    SkillRegistry,
)

CaseFn = Callable[[], None]


class SkipCase(Exception):
    """用例主动跳过。"""


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


# ---------- env ----------

def case_env_config_priority() -> None:
    prev = cfg.get_config()
    try:
        cfg.set_config({"env": {"SA_REG_ENV": "from-config"}})
        value = EnvLayer().get("SA_REG_ENV")
        _assert(value == "from-config", f"期望 from-config，实际 {value!r}")
    finally:
        cfg.set_config(prev)


def case_env_process_fallback() -> None:
    import os

    prev = cfg.get_config()
    key = "SA_REG_PROCESS_ONLY"
    old = os.environ.get(key)
    try:
        cfg.set_config({})
        os.environ[key] = "from-process"
        value = get_env(key, quiet=True)
        _assert(value == "from-process", f"期望 from-process，实际 {value!r}")
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
        cfg.set_config(prev)


def case_env_missing_empty() -> None:
    prev = cfg.get_config()
    try:
        cfg.set_config({})
        value = get_env("SA_REG_NOT_EXISTS_XYZ", default="", quiet=True)
        _assert(value == "", f"未找到应返回空串，实际 {value!r}")
    finally:
        cfg.set_config(prev)


# ---------- log ----------

def case_log_levels_and_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reg.log"
        logger = Logger(
            name="regression",
            level=LogLevel.NOTICE,
            modes=["file"],
            file_path=path,
        )
        logger.notice("n1")
        logger.warning("w1")
        logger.critical("c1")
        text = path.read_text(encoding="utf-8")
        _assert("提醒" in text and "n1" in text, "提醒日志未写入")
        _assert("警告" in text and "w1" in text, "警告日志未写入")
        _assert("严重" in text and "c1" in text, "严重日志未写入")


def case_log_level_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reg.log"
        logger = Logger(
            name="regression",
            level=LogLevel.WARNING,
            modes=["file"],
            file_path=path,
        )
        logger.notice("should-hide")
        logger.warning("should-show")
        text = path.read_text(encoding="utf-8")
        _assert("should-hide" not in text, "低于阈值的提醒不应写出")
        _assert("should-show" in text, "警告应写出")


# ---------- skills ----------

def case_skill_local_file_crud() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill = LocalFileSkill(root=root, allow_write=True)
        w = skill.run(action="write", path="demo.txt", content="hello regression")
        _assert(w.ok, w.output)
        r = skill.run(action="read", path="demo.txt")
        _assert(r.ok and "hello regression" in r.output, r.output)
        p = skill.run(
            action="patch",
            path="demo.txt",
            old_text="regression",
            new_text="selfagent",
        )
        _assert(p.ok, p.output)
        listed = skill.run(action="list", path=".")
        _assert(listed.ok and "demo.txt" in listed.output, listed.output)
        final = (root / "demo.txt").read_text(encoding="utf-8")
        _assert(final == "hello selfagent", f"内容不符: {final!r}")
        moved = skill.run(action="move", path="demo.txt", dest="nested/demo2.txt")
        _assert(moved.ok, moved.output)
        _assert(not (root / "demo.txt").exists(), "源文件应已消失")
        _assert(
            (root / "nested" / "demo2.txt").read_text(encoding="utf-8")
            == "hello selfagent",
            "移动后内容不一致",
        )


def case_skill_path_escape_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skill = LocalFileSkill(root=tmp, allow_write=False)
        result = skill.run(action="read", path="../outside.txt")
        _assert(not result.ok, "越界路径应失败")
        _assert("越界" in result.output, result.output)


def case_skill_ask_user_choice() -> None:
    skill = AskUserSkill(ask_handler=lambda q, opts, meta: "2")
    result = skill.run(
        question="落地前选方案",
        options=["方案甲", "方案乙", "方案丙"],
    )
    _assert(result.ok, result.output)
    data = json.loads(result.output)
    _assert(data["selected"] == ["方案乙"], data)


def case_skill_request_capability() -> None:
    from unittest.mock import MagicMock

    with tempfile.TemporaryDirectory() as tmp:
        bot = MagicMock()
        bot.send_markdown.return_value = MagicMock(ok=True, code=0, msg="ok", raw={})
        skill = RequestCapabilitySkill(output_dir=Path(tmp) / "reqs", bot=bot)
        result = skill.run(
            title="需要浏览器",
            need="打开页面",
            why="现有 Skill 不足",
            workaround="先手工验证",
        )
        _assert(result.ok, result.output)
        data = json.loads(result.output)
        _assert(Path(data["path"]).is_file(), data["path"])
        _assert(data["notified"] is True, data)
        _assert("继续用现有 Skill" in data["reminder"], data)


def case_skill_search_code() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "demo.py").write_text("class PlanMode:\n    pass\n", encoding="utf-8")
        skill = SearchCodeSkill(root=root)
        result = skill.run(query="PlanMode", extensions=["py"])
        _assert(result.ok, result.output)
        data = json.loads(result.output)
        _assert(data["count"] >= 1, data)


def case_skill_shell_run_safe() -> None:
    import sys
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        exe = sys.executable
        allow = ["python", "python3", "py", _Path(exe).name.lower().replace(".exe", "")]
        skill = ShellRunSkill(root=tmp, allow_commands=allow)
        ok = skill.run(argv=[exe, "-c", "print(12345)"])
        _assert(ok.ok, ok.output)
        _assert("12345" in ok.output, ok.output)
        bad = skill.run(command=f'{exe} -c "print(1)" && {exe} -c "print(2)"')
        _assert(not bad.ok, "应拦截危险命令片段")


def case_skill_feishu_notify_missing_webhook() -> None:
    from feishu import FeishuBot

    bot = FeishuBot(webhook_url="")
    skill = FeishuNotifySkill(bot=bot)
    result = skill.run(action="text", text="regression")
    _assert(not result.ok, "无 webhook 应失败")


def case_skill_git_ops_readonly() -> None:
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        for args in (
            ["git", "init"],
            ["git", "config", "user.email", "reg@example.com"],
            ["git", "config", "user.name", "Reg"],
        ):
            subprocess.run(args, cwd=str(repo), check=True, capture_output=True)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "f.txt"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        skill = GitOpsSkill(root=repo, allow_write=False)
        status = skill.run(action="status")
        _assert(status.ok, status.output)
        blocked = skill.run(action="add", paths=["f.txt"])
        _assert(not blocked.ok, "只读模式应禁止 add")


# ---------- permission ----------

def case_permission_readonly_blocks_write() -> None:
    guard = PermissionGuard(
        enabled=True,
        role="readonly",
        default_effect="deny",
        roles={
            "readonly": RolePolicy(
                name="readonly",
                skills={"local_file": SkillRule(allow=True, actions={"list", "read"})},
            )
        },
    )
    _assert(
        guard.check("local_file", arguments={"action": "read", "path": "a"}).allowed,
        "readonly 应允许 read",
    )
    denied = guard.check("local_file", arguments={"action": "write", "path": "a"})
    _assert(not denied.allowed, "readonly 应拒绝 write")

    with tempfile.TemporaryDirectory() as tmp:
        reg = SkillRegistry(permission=guard)
        reg.register(LocalFileSkill(root=tmp, allow_write=True))
        blocked = reg.run(
            "local_file",
            {"action": "write", "path": "x.txt", "content": "no"},
        )
        _assert(not blocked.ok and "权限拒绝" in blocked.output, blocked.output)


def case_permission_react_enforcement() -> None:
    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: write\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"write","path":"z.txt","content":"x"}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content="Thought: done\nFinal Answer: ok",
                model="s",
                provider=self.provider,
            )

    guard = PermissionGuard(
        enabled=True,
        role="readonly",
        default_effect="deny",
        roles={
            "readonly": RolePolicy(
                name="readonly",
                skills={"local_file": SkillRule(allow=True, actions={"list", "read"})},
            )
        },
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reg = SkillRegistry()
        reg.register(LocalFileSkill(root=root, allow_write=True))
        agent = ReActAgent(
            ai=_AI(),
            skills=reg,
            permission=guard,
            max_steps=4,
            system_prompt="reg",
        )
        result = agent.run("写文件")
        _assert(result.completed, "应完成")
        _assert(not (root / "z.txt").exists(), "权限应阻止写入")
        _assert(
            any("权限拒绝" in (c.observation or "") for c in result.steps[0].calls),
            "应返回权限拒绝观察",
        )


# ---------- react parser / agent ----------

def case_react_parse_multi_action() -> None:
    text = """Thought: 多工具
Action: local_file
Action Input: {"action":"list","path":"."}
Action: local_file
Action Input: {"action":"read","path":"a.txt"}
"""
    parsed = parse_react_output(text)
    _assert(len(parsed.actions) == 2, f"应解析 2 个 Action，实际 {len(parsed.actions)}")
    _assert(parsed.final_answer is None, "不应有 Final Answer")


def case_react_agent_multi_action() -> None:
    class _ScriptedAI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self._n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self._n += 1
            if self._n == 1:
                content = """Thought: 读两个文件
Action: local_file
Action Input: {"action":"read","path":"x.txt"}
Action: local_file
Action Input: {"action":"read","path":"y.txt"}
"""
            else:
                content = "Thought: 完成\nFinal Answer: ok-reg"
            return AIResponse(content=content, model="scripted", provider=self.provider)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "x.txt").write_text("X", encoding="utf-8")
        (root / "y.txt").write_text("Y", encoding="utf-8")
        registry = SkillRegistry()
        registry.register(LocalFileSkill(root=root, allow_write=False))
        agent = ReActAgent(
            ai=_ScriptedAI(),
            skills=registry,
            permission=PermissionGuard.allow_all(),
            max_steps=5,
            system_prompt="regression",
        )
        result = agent.run("读 x 和 y")
        _assert(result.completed, "应完成")
        _assert(result.answer == "ok-reg", result.answer)
        _assert(len(result.steps[0].calls) == 2, "第一步应有 2 个调用")


def case_plan_mode_parse_and_block_write() -> None:
    text = """Thought: 规划
Plan:
1. skill=local_file | input={"action":"write","path":"p.txt","content":"x"} | why=写入
Final Answer: 计划完成
"""
    plan = parse_plan(text, final_answer="计划完成")
    _assert(plan.ok and len(plan.steps) == 1, "应解析到 1 个计划步骤")

    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            if self.n == 1:
                return AIResponse(
                    content=(
                        "Thought: 写入\n"
                        "Action: local_file\n"
                        'Action Input: {"action":"write","path":"p.txt","content":"x"}\n'
                    ),
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(content=text, model="s", provider=self.provider)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        reg = SkillRegistry()
        reg.register(LocalFileSkill(root=root, allow_write=True))
        agent = ReActAgent(
            ai=_AI(),
            skills=reg,
            permission=PermissionGuard.allow_all(),
            mode=AgentMode.PLAN,
            max_steps=4,
        )
        result = agent.plan("做计划")
        _assert(result.completed and result.plan.ok, "Plan Mode 应产出计划")
        _assert(not (root / "p.txt").exists(), "Plan Mode 不应真正写入")

        exec_agent = ReActAgent(
            ai=_AI(),
            skills=reg,
            permission=PermissionGuard.allow_all(),
            mode=AgentMode.AGENT,
        )
        executed = exec_agent.execute_plan(
            Plan(
                summary="执行",
                steps=[
                    PlanStep(
                        index=1,
                        skill="local_file",
                        arguments={
                            "action": "write",
                            "path": "p.txt",
                            "content": "planned",
                        },
                    )
                ],
            )
        )
        _assert(executed.completed, executed.answer)
        _assert(
            (root / "p.txt").read_text(encoding="utf-8") == "planned",
            "execute_plan 应写入文件",
        )


def case_conversation_multi_turn() -> None:
    class _AI(AIClient):
        provider = "scripted"

        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, options: ChatOptions | None = None) -> AIResponse:
            self.n += 1
            blob = "\n".join(m.content for m in messages)
            if "secret-42" in blob and self.n >= 2:
                return AIResponse(
                    content="Final Answer: got-secret-42",
                    model="s",
                    provider=self.provider,
                )
            return AIResponse(
                content="Final Answer: secret-42",
                model="s",
                provider=self.provider,
            )

    agent = ReActAgent(
        ai=_AI(),
        skills=SkillRegistry(),
        permission=PermissionGuard.allow_all(),
        mode=AgentMode.AGENT,
        max_steps=3,
    )
    conv = Conversation(agent, inject_continuous_hint=False)
    r1 = conv.chat("记住 secret")
    r2 = conv.chat("复述")
    _assert(r1.answer == "secret-42", r1.answer)
    _assert(r2.answer == "got-secret-42", r2.answer)
    _assert(conv.turn_count == 2, "应记录两轮")


# ---------- ai ----------

def case_ai_factory_unknown_provider() -> None:
    try:
        create_ai_client("not-a-real-provider")
    except ValueError:
        return
    raise AssertionError("未知提供商应抛 ValueError")


def case_ai_deepseek_live() -> None:
    from env import get_env

    ai_cfg = (cfg.get_section("ai", {}) or {}).get("deepseek") or {}
    key = str(ai_cfg.get("api_key") or "") or get_env(
        "DEEPSEEK_API_KEY", default="", quiet=True
    )
    if not key:
        raise SkipCase("未配置 ai.deepseek.api_key，跳过 AI 在线回归")

    client = create_ai_client("deepseek")
    reply = client.ask("只回复一个字：好", max_tokens=16)
    _assert(bool(reply and reply.strip()), "DeepSeek 返回为空")


# ---------- feishu ----------

def case_feishu_missing_webhook() -> None:
    prev = cfg.get_config()
    try:
        cfg.set_config({"feishu": {"webhook_url": ""}})
        bot = FeishuBot(webhook_url="")
        resp = bot.send_text("regression")
        _assert(not resp.ok, "无 webhook 应失败")
        _assert("webhook" in resp.msg.lower() or resp.msg == "webhook missing", resp.msg)
    finally:
        cfg.set_config(prev)


def case_feishu_live() -> None:
    from env import get_env

    feishu_cfg = cfg.get_section("feishu", {}) or {}
    url = str(feishu_cfg.get("webhook_url") or "") or get_env(
        "FEISHU_WEBHOOK_URL", default="", quiet=True
    )
    if not url:
        raise SkipCase("未配置 feishu.webhook_url，跳过飞书在线回归")

    bot = FeishuBot(webhook_url=url)
    resp = bot.send_text("[SelfAgent 回归] 飞书模块连通性检查")
    _assert(resp.ok, f"飞书推送失败: {resp.msg} raw={json.dumps(resp.raw, ensure_ascii=False)}")


def build_cases(*, include_live: bool = False) -> list[tuple[str, str, CaseFn]]:
    """返回 (module, name, fn)。"""
    cases: list[tuple[str, str, CaseFn]] = [
        ("env", "配置优先查找", case_env_config_priority),
        ("env", "进程环境兜底", case_env_process_fallback),
        ("env", "缺失返回空值", case_env_missing_empty),
        ("log", "三级日志写入文件", case_log_levels_and_file),
        ("log", "等级过滤", case_log_level_filter),
        ("skills", "local_file 读写改查", case_skill_local_file_crud),
        ("skills", "路径越界拦截", case_skill_path_escape_blocked),
        ("skills", "ask_user 方案选择", case_skill_ask_user_choice),
        ("skills", "request_capability 提需求", case_skill_request_capability),
        ("skills", "search_code 检索", case_skill_search_code),
        ("skills", "shell_run 白名单", case_skill_shell_run_safe),
        ("skills", "feishu_notify 缺配置", case_skill_feishu_notify_missing_webhook),
        ("skills", "git_ops 只读", case_skill_git_ops_readonly),
        ("permission", "只读角色拦截写入", case_permission_readonly_blocks_write),
        ("permission", "ReAct 权限执行拦截", case_permission_react_enforcement),
        ("react", "多 Action 解析", case_react_parse_multi_action),
        ("react", "多 Action 智能体执行", case_react_agent_multi_action),
        ("react", "Plan Mode 规划与执行", case_plan_mode_parse_and_block_write),
        ("react", "连续对话多轮上下文", case_conversation_multi_turn),
        ("ai", "未知提供商报错", case_ai_factory_unknown_provider),
        ("feishu", "未配置 webhook 失败", case_feishu_missing_webhook),
    ]
    if include_live:
        cases.extend(
            [
                ("ai", "DeepSeek 在线对话", case_ai_deepseek_live),
                ("feishu", "飞书在线推送", case_feishu_live),
            ]
        )
    return cases


def filter_cases(
    cases: list[tuple[str, str, CaseFn]],
    *,
    modules: list[str] | None = None,
) -> list[tuple[str, str, CaseFn]]:
    if not modules:
        return cases
    allow = {m.strip().lower() for m in modules if m.strip()}
    return [c for c in cases if c[0].lower() in allow]
