"""Hook 事件枚举（对齐 CC HookEvent 核心全集）。"""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PERMISSION_REQUEST = "PermissionRequest"
    # 文件/配置类占位（可注册，无触发点时 no-op）
    FILE_CHANGED = "FileChanged"
    CONFIG_CHANGE = "ConfigChange"
    NOTIFICATION = "Notification"

    @classmethod
    def parse(cls, value: str | "HookEvent" | None) -> "HookEvent | None":
        if value is None:
            return None
        if isinstance(value, HookEvent):
            return value
        raw = str(value).strip()
        for item in cls:
            if item.value == raw or item.name == raw:
                return item
        # 宽松别名
        aliases = {
            "pre_tool": cls.PRE_TOOL_USE,
            "pretooluse": cls.PRE_TOOL_USE,
            "post_tool": cls.POST_TOOL_USE,
            "stop": cls.STOP,
        }
        key = raw.lower().replace("-", "").replace("_", "")
        for k, v in aliases.items():
            if k.replace("_", "") == key:
                return v
        return None


ALL_HOOK_EVENTS: tuple[HookEvent, ...] = tuple(HookEvent)
