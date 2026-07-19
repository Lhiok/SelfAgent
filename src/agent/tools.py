"""Skill → OpenAI tools schema；内置 finish / submit_plan。"""

from __future__ import annotations

from typing import Any

from permission import PermissionGuard
from skills.registry import SkillRegistry

FINISH_TOOL = "finish"
SUBMIT_PLAN_TOOL = "submit_plan"
ENTER_PLAN_MODE_TOOL = "enter_plan_mode"

# 可并行的只读类 skill（其余串行）
_SAFE_SKILLS = frozenset(
    {
        "local_file",  # 仍按 action 再判；默认保守：仅 list/read 安全
        "search_code",
        "web_fetch",
        "diff_review",
        "git_ops",
        "todo_tracker",
        "screenshot",
        "nodejs",
        "python",
        "csharp",
    }
)
_SAFE_ACTIONS = frozenset(
    {
        "list",
        "read",
        "search",
        "fetch",
        "review",
        "status",
        "diff",
        "log",
        "branch",
        "show",
        "version",
        "compile",
    }
)


DEFAULT_TOOL_SYSTEM_PROMPT = """你是可调用工具的助手。请通过 function/tool 调用完成任务。

规则：
1. 需要读文件、执行命令、提问等时，调用对应工具。
2. 任务完成时必须调用 finish，参数 {"answer": "最终答复"}。
3. 可在一轮中发起多个互不依赖的工具调用。
4. 复杂改动前可调用 enter_plan_mode 进入只读规划，再 submit_plan 交用户确认。
5. ask_user：可连续调用收集问题；系统会暂存，在 finish / submit_plan / 其它工具前一次性询问用户。
6. todo_tracker：多步骤任务先 add，再 start → 干活 → complete。
"""

from plan.prompts import PLAN_MODE_SECTION as PLAN_TOOL_SYSTEM_PROMPT


def build_tool_schemas(
    skills: SkillRegistry,
    permission: PermissionGuard | None = None,
    *,
    include_submit_plan: bool = False,
    include_enter_plan: bool = False,
) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    names = list(skills.names())
    if permission is not None:
        names = permission.allowed_skills(names)

    for name in sorted(names):
        skill = skills.get(name)
        if skill is None:
            continue
        params = skill.parameters_schema or {"type": "object", "properties": {}}
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": skill.name,
                    "description": skill.description or skill.name,
                    "parameters": params,
                },
            }
        )

    schemas.append(
        {
            "type": "function",
            "function": {
                "name": FINISH_TOOL,
                "description": "结束本轮任务并给出最终答复",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "description": "最终答复"},
                    },
                    "required": ["answer"],
                },
            },
        }
    )
    if include_enter_plan:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": ENTER_PLAN_MODE_TOOL,
                    "description": (
                        "进入 Plan Mode（只读调研）。复杂改动前先调用；"
                        "调研后用 submit_plan 提交计划供用户确认。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "为何需要进入规划模式",
                            },
                        },
                    },
                },
            }
        )
    if include_submit_plan:
        from plan.tools import SUBMIT_PLAN_DESCRIPTION

        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": SUBMIT_PLAN_TOOL,
                    "description": SUBMIT_PLAN_DESCRIPTION,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "thought": {"type": "string"},
                            "steps": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "skill": {"type": "string"},
                                        "input": {"type": "object"},
                                        "why": {"type": "string"},
                                    },
                                    "required": ["skill"],
                                },
                            },
                        },
                        "required": ["summary", "steps"],
                    },
                },
            }
        )
    return schemas


def is_concurrency_safe(skill_name: str, arguments: dict[str, Any] | None = None) -> bool:
    name = (skill_name or "").strip()
    if name in {
        FINISH_TOOL,
        SUBMIT_PLAN_TOOL,
        ENTER_PLAN_MODE_TOOL,
        "ask_user",
        "shell_run",
        "browser",
        "run_subagent",
    }:
        return False
    if name.startswith("mcp__"):
        return False
    if name not in _SAFE_SKILLS:
        return False
    args = arguments or {}
    action = str(args.get("action") or "").strip().lower()
    if not action:
        return name in {"search_code", "web_fetch", "diff_review"}
    if name == "local_file":
        return action in {"list", "read"}
    if name == "git_ops":
        return action in {"status", "diff", "log", "branch", "show"}
    return action in _SAFE_ACTIONS
