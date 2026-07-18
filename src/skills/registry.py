"""Skill 注册与调度。"""

from __future__ import annotations

import json
from typing import Any, Iterable

import config as cfg
from log import get_logger
from permission import PermissionGuard
from skills.ask_user import AskUserSkill
from skills.base import Skill, SkillResult
from skills.feishu_notify import FeishuNotifySkill
from skills.git_ops import GitOpsSkill
from skills.local_file import LocalFileSkill
from skills.request_capability import RequestCapabilitySkill
from skills.search_code import SearchCodeSkill
from skills.shell_run import ShellRunSkill

logger = get_logger("skills")


class SkillRegistry:
    def __init__(self, permission: PermissionGuard | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        self.permission = permission

    def set_permission(self, permission: PermissionGuard | None) -> None:
        self.permission = permission

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        logger.notice(f"注册 Skill: {skill.name}")

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def schema_map(self) -> dict[str, str]:
        return {name: skill.schema_text() for name, skill in self._skills.items()}

    def list_schemas(self, permission: PermissionGuard | None = None) -> str:
        guard = permission if permission is not None else self.permission
        if not self._skills:
            return "(无可用 Skill)"
        mapping = self.schema_map()
        if guard is None:
            return "\n".join(mapping[n] for n in sorted(mapping))
        return guard.filter_schema_text(mapping)

    def run(
        self,
        name: str,
        arguments: dict[str, Any] | str | None = None,
        *,
        permission: PermissionGuard | None = None,
        enforce_permission: bool = True,
    ) -> SkillResult:
        guard = permission if permission is not None else self.permission
        if enforce_permission and guard is not None:
            decision = guard.assert_allowed(name, arguments=arguments)
            if not decision.allowed:
                return SkillResult(ok=False, output=f"权限拒绝: {decision.reason}")

        skill = self.get(name)
        if skill is None:
            logger.warning(f"未知 Skill: {name}")
            return SkillResult(ok=False, output=f"未知 Skill: {name}")

        args: dict[str, Any]
        if arguments is None:
            args = {}
        elif isinstance(arguments, str):
            text = arguments.strip()
            if not text:
                args = {}
            else:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    return SkillResult(ok=False, output=f"Action Input 不是合法 JSON: {exc}")
                if not isinstance(parsed, dict):
                    return SkillResult(ok=False, output="Action Input 必须是 JSON 对象")
                args = parsed
        else:
            args = arguments

        try:
            return skill.run(**args)
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"Skill 执行异常 [{name}]: {exc}")
            return SkillResult(ok=False, output=f"Skill 执行异常: {exc}")

    @classmethod
    def from_config(
        cls,
        skills: Iterable[Skill] | None = None,
        *,
        permission: PermissionGuard | None = None,
        load_permission: bool = True,
    ) -> "SkillRegistry":
        guard = permission
        if guard is None and load_permission:
            try:
                guard = PermissionGuard.from_config()
            except Exception:  # noqa: BLE001
                guard = PermissionGuard.allow_all()

        registry = cls(permission=guard)
        if skills is not None:
            for skill in skills:
                registry.register(skill)
            return registry

        section = cfg.get_section("skills", {}) or {}
        lf = section.get("local_file") or {}
        root = lf.get("root", ".")
        registry.register(
            LocalFileSkill(
                root=root,
                allow_write=bool(lf.get("allow_write", True)),
            )
        )

        ask_cfg = section.get("ask_user") or {}
        if bool(ask_cfg.get("enabled", True)):
            registry.register(AskUserSkill())

        rc_cfg = section.get("request_capability") or {}
        if bool(rc_cfg.get("enabled", True)):
            registry.register(
                RequestCapabilitySkill(
                    output_dir=rc_cfg.get("output_dir", "requirements"),
                    notify_feishu=bool(rc_cfg.get("notify_feishu", True)),
                    enabled=True,
                )
            )

        sc_cfg = section.get("search_code") or {}
        if bool(sc_cfg.get("enabled", True)):
            registry.register(
                SearchCodeSkill(
                    root=sc_cfg.get("root", root),
                    skip_dirs=set(sc_cfg.get("skip_dirs") or []) or None,
                )
            )

        sh_cfg = section.get("shell_run") or {}
        if bool(sh_cfg.get("enabled", True)):
            registry.register(
                ShellRunSkill(
                    root=sh_cfg.get("root", root),
                    allow_commands=sh_cfg.get("allow_commands"),
                    deny_substrings=sh_cfg.get("deny_substrings"),
                    default_timeout=float(sh_cfg.get("timeout", 60)),
                    max_output_chars=int(sh_cfg.get("max_output_chars", 20000)),
                    enabled=True,
                )
            )

        fn_cfg = section.get("feishu_notify") or {}
        if bool(fn_cfg.get("enabled", True)):
            registry.register(FeishuNotifySkill(enabled=True))

        go_cfg = section.get("git_ops") or {}
        if bool(go_cfg.get("enabled", True)):
            registry.register(
                GitOpsSkill(
                    root=go_cfg.get("root", root),
                    allow_write=bool(go_cfg.get("allow_write", False)),
                    git_bin=str(go_cfg.get("git_bin") or "git"),
                    timeout=float(go_cfg.get("timeout", 60)),
                    max_output_chars=int(go_cfg.get("max_output_chars", 30000)),
                    enabled=True,
                )
            )

        return registry
