"""统一权限决策流水线（对齐 hasPermissionsToUseTool 核心顺序）。"""

from __future__ import annotations

import json
from typing import Any

from permission.context import PermissionContext
from permission.modes import apply_dont_ask, mode_may_allow
from permission.rules import any_rule_matches
from permission.types import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionUpdate,
)


def evaluate(
    ctx: PermissionContext,
    skill: str,
    *,
    action: str | None = None,
    arguments: dict[str, Any] | str | None = None,
) -> PermissionDecision:
    skill_name = (skill or "").strip()
    if not skill_name:
        return PermissionDecision.deny("Skill 名为空")

    args = normalize_args(arguments)
    op = (action or args.get("action") or "").strip().lower() or None
    plan_on = ctx.plan_active or ctx.mode is PermissionMode.PLAN

    # 未启用且非 Plan：全部放行
    if not ctx.enabled and not plan_on:
        return PermissionDecision.allow("权限控制未启用")

    # 1) deny（启用时；免疫 bypass）
    if ctx.enabled:
        hit = any_rule_matches(ctx.all_deny(), skill_name, op)
        if hit is not None:
            return PermissionDecision.deny(
                f"命中 deny 规则: {hit.raw or hit.key()}",
                matched_rule=hit.raw or hit.key(),
            )

        # 1b) 会话已授权：短路 ask（用户本轮已确认）
        hit = any_rule_matches(ctx.session_allow, skill_name, op)
        if hit is not None:
            return PermissionDecision.allow(
                f"命中会话 allow: {hit.raw or hit.key()}",
                matched_rule=hit.raw or hit.key(),
            )

        # 2) ask（免疫 bypass；dont_ask → deny）
        hit = any_rule_matches(ctx.ask_rules, skill_name, op)
        if hit is not None:
            suggestion = PermissionUpdate(
                behavior=PermissionBehavior.ALLOW,
                rule_raw=ctx.suggest_allow(skill_name, op),
                destination="session",
            )
            decision = PermissionDecision.ask(
                f"命中 ask 规则: {hit.raw or hit.key()}（可用 grant_session 放行）",
                matched_rule=hit.raw or hit.key(),
                suggestions=[suggestion],
            )
            if ctx.mode is PermissionMode.DONT_ASK:
                return apply_dont_ask(decision)
            return decision

    # 3) Hooks PreToolUse + 程序化 pre_tool_hooks（registry 始终参与）
    from agent.hooks import run_pre_tool_hooks

    hooked = run_pre_tool_hooks(
        ctx.pre_tool_hooks,
        skill_name,
        action=op,
        arguments=args,
    )
    if hooked is not None:
        return hooked

    # 4a) Plan Mode：即使 enabled=False 也强制只读门禁（产品安全）
    if plan_on:
        return _evaluate_plan(ctx, skill_name, op)

    if not ctx.enabled:
        return PermissionDecision.allow("权限控制未启用")

    # 4b) mode 快速放行（bypass / accept_edits）
    mode_hit = mode_may_allow(
        ctx.mode,
        skill=skill_name,
        action=op,
        arguments=args,
        workdir=ctx.workdir,
    )
    if mode_hit is not None:
        return mode_hit

    # 5) allow / session once
    hit = any_rule_matches(ctx.all_allow(), skill_name, op)
    if hit is not None:
        return PermissionDecision.allow(
            f"命中 allow 规则: {hit.raw or hit.key()}",
            matched_rule=hit.raw or hit.key(),
        )

    # 6) 默认
    if ctx.default_effect == "allow":
        return PermissionDecision.allow("default_effect=allow")
    suggestion = PermissionUpdate(
        behavior=PermissionBehavior.ALLOW,
        rule_raw=ctx.suggest_allow(skill_name, op),
        destination="session",
    )
    return PermissionDecision.deny(
        f"未授权 Skill: {skill_name}" + (f".{op}" if op else ""),
        suggestions=[suggestion],
    )


def _evaluate_plan(
    ctx: PermissionContext,
    skill: str,
    action: str | None,
) -> PermissionDecision:
    if skill in ctx.plan_allow_skills:
        return PermissionDecision.allow(
            f"Plan Mode allow_skills: {skill}",
            matched_rule="plan:allow_skills",
        )
    if not ctx.allow_readonly_in_plan:
        return PermissionDecision.deny(
            f"Plan Mode 禁止执行非只读操作: {skill}",
            matched_rule="plan:readonly_off",
        )
    allowed_ops = ctx.readonly_actions.get(skill) or ctx.readonly_actions.get("*")
    if not allowed_ops:
        return PermissionDecision.deny(
            f"Plan Mode 禁止执行非只读操作: {skill}",
            matched_rule="plan:no_readonly",
        )
    if action is None:
        return PermissionDecision.allow(
            f"Plan Mode 只读 Skill: {skill}",
            matched_rule="plan:readonly_skill",
        )
    if action in allowed_ops or "*" in allowed_ops:
        return PermissionDecision.allow(
            f"Plan Mode 只读: {skill}.{action}",
            matched_rule="plan:readonly_action",
        )
    return PermissionDecision.deny(
        f"Plan Mode 禁止执行非只读操作: {skill}.{action}。"
        f"请将写入类操作写入 Plan 步骤，而不是 Action。",
        matched_rule="plan:block_write",
    )


def normalize_args(arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    text = str(arguments).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
