"""SkillBridge：统一调度、截断、取消注入与进度事件。"""

from __future__ import annotations

import json
from typing import Any, Callable

import config as cfg
from log import get_logger
from permission import PermissionGuard
from skills.base import SkillResult
from skills.registry import SkillRegistry

logger = get_logger("skills.bridge")

ProgressHandler = Callable[[dict[str, Any]], None]


def _truncate_output(
    result: SkillResult,
    *,
    max_chars: int | None,
    max_bytes: int | None,
) -> SkillResult:
    text = result.output or ""
    original_len = len(text)
    truncated = False

    if max_chars is not None and max_chars > 0 and len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
        truncated = True

    if max_bytes is not None and max_bytes > 0:
        raw = text.encode("utf-8", errors="replace")
        if len(raw) > max_bytes:
            # 按字节截断，避免切断多字节字符
            raw = raw[:max_bytes]
            while raw:
                try:
                    text = raw.decode("utf-8") + "…"
                    break
                except UnicodeDecodeError:
                    raw = raw[:-1]
            else:
                text = "…"
            truncated = True

    if not truncated:
        return result

    data = dict(result.data or {})
    data["truncated"] = True
    data["original_len"] = original_len
    return SkillResult(ok=result.ok, output=text, data=data)


def _strip_json_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if not lines:
        return s
    # 去掉首行 ``` 或 ```json
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def parse_arguments(arguments: dict[str, Any] | str | None) -> tuple[dict[str, Any] | None, str | None]:
    """解析 Action Input；成功返回 (args, None)，失败返回 (None, error)。"""
    if arguments is None:
        return {}, None
    if isinstance(arguments, dict):
        return arguments, None
    text = str(arguments).strip()
    if not text:
        return {}, None
    candidates = [text, _strip_json_fence(text)]
    last_err: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
        if not isinstance(parsed, dict):
            return None, "Action Input 必须是 JSON 对象"
        return parsed, None
    return None, f"Action Input 不是合法 JSON: {last_err}"


class SkillBridge:
    """包装 SkillRegistry，提供统一 invoke 边界。"""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        output_max_chars: int | None = None,
        output_max_bytes: int | None = None,
        mcp_manager: Any | None = None,
    ) -> None:
        self.registry = registry
        self.mcp_manager = mcp_manager
        self._subagent_ctx: dict[str, Any] = {}
        skills_cfg = cfg.get_section("skills", {}) or {}
        self.output_max_chars = (
            output_max_chars
            if output_max_chars is not None
            else int(skills_cfg.get("output_max_chars", 20000) or 0) or None
        )
        raw_bytes = skills_cfg.get("output_max_bytes")
        self.output_max_bytes = (
            output_max_bytes
            if output_max_bytes is not None
            else (int(raw_bytes) if raw_bytes not in (None, "") else None)
        )

    def set_mcp_manager(self, manager: Any | None) -> None:
        self.mcp_manager = manager

    def bind_subagent_context(self, **kwargs: Any) -> None:
        self._subagent_ctx.update(kwargs)

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | str | None = None,
        *,
        permission: PermissionGuard | None = None,
        enforce_permission: bool = True,
        control: Any | None = None,
        on_progress: ProgressHandler | None = None,
    ) -> SkillResult:
        guard = permission if permission is not None else self.registry.permission
        args, err = parse_arguments(arguments)
        if err is not None:
            return SkillResult(ok=False, output=err)

        assert args is not None
        if enforce_permission and guard is not None:
            from permission.types import PermissionBehavior

            decision = guard.check(name, arguments=args)
            if decision.behavior is PermissionBehavior.ASK:
                allowed = self._resolve_ask(guard, name, args, decision, on_progress)
                if not allowed:
                    tip = ""
                    if decision.suggestions:
                        rules = ", ".join(s.rule_raw for s in decision.suggestions)
                        tip = f"；可会话放行: grant_session({rules!r})"
                    return SkillResult(
                        ok=False,
                        output=f"权限拒绝: {decision.reason}{tip}",
                    )
            elif not decision.allowed:
                tip = ""
                if getattr(decision, "suggestions", None):
                    rules = ", ".join(s.rule_raw for s in decision.suggestions)
                    tip = f"；会话放行: grant_session({rules!r})"
                logger.warning(f"权限拒绝: {decision.reason}{tip}")
                return SkillResult(
                    ok=False, output=f"权限拒绝: {decision.reason}{tip}"
                )

        if name == "run_subagent":
            result = self._invoke_subagent(args, on_progress=on_progress)
            return _truncate_output(
                result,
                max_chars=self.output_max_chars,
                max_bytes=self.output_max_bytes,
            )

        from mcp.pool import is_mcp_tool

        if is_mcp_tool(name):
            result = self._invoke_mcp(name, args)
            result = _truncate_output(
                result,
                max_chars=self.output_max_chars,
                max_bytes=self.output_max_bytes,
            )
            self._emit_post_tool(name, args, result)
            return result

        skill = self.registry.get(name)
        if skill is None:
            logger.warning(f"未知 Skill: {name}")
            return SkillResult(ok=False, output=f"未知 Skill: {name}")

        if on_progress is not None:
            try:
                on_progress(
                    {
                        "type": "skill",
                        "skill": name,
                        "status": "start",
                        "message": f"开始执行 {name}",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"skill progress 回调失败: {exc}")

        run_kwargs = dict(args)
        if control is not None:
            run_kwargs["_control"] = control

        try:
            # 协作式 skill 可接收 _control；不支持则去掉再调
            try:
                result = skill.run(**run_kwargs)
            except TypeError:
                run_kwargs.pop("_control", None)
                result = skill.run(**run_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"Skill 执行异常 [{name}]: {exc}")
            result = SkillResult(ok=False, output=f"Skill 执行异常: {exc}")

        result = _truncate_output(
            result,
            max_chars=self.output_max_chars,
            max_bytes=self.output_max_bytes,
        )

        if on_progress is not None:
            try:
                on_progress(
                    {
                        "type": "skill",
                        "skill": name,
                        "status": "end",
                        "ok": result.ok,
                        "message": f"完成 {name}",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"skill progress 回调失败: {exc}")

        self._emit_post_tool(name, args, result)
        return result

    def _invoke_mcp(self, name: str, args: dict[str, Any]) -> SkillResult:
        mgr = self.mcp_manager
        if mgr is None:
            return SkillResult(ok=False, output="MCP 未配置或未连接")
        ok, text, data = mgr.call(name, args)
        return SkillResult(ok=ok, output=text, data=data)

    def _invoke_subagent(
        self,
        args: dict[str, Any],
        *,
        on_progress: ProgressHandler | None = None,
    ) -> SkillResult:
        from subagent.spawn import run_subagent

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return SkillResult(ok=False, output="run_subagent 需要 prompt")
        ctx = self._subagent_ctx
        ai = ctx.get("ai")
        skills = ctx.get("skills") or self.registry
        permission = ctx.get("permission")
        if ai is None or permission is None:
            return SkillResult(
                ok=False,
                output="run_subagent 未绑定父 Agent 上下文（ai/permission）",
            )
        try:
            out = run_subagent(
                prompt=prompt,
                ai=ai,
                skills=skills,
                permission=permission,
                bridge=SkillBridge(skills, mcp_manager=self.mcp_manager),
                description=str(args.get("description") or ""),
                max_steps=int(args.get("max_steps") or 8),
                agent_id=str(args.get("agent_id") or "") or None,
                session_id=str(ctx.get("session_id") or ""),
                workdir=ctx.get("workdir"),
                on_progress=on_progress or ctx.get("on_progress"),
                mcp_manager=ctx.get("mcp_manager") or self.mcp_manager,
            )
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"run_subagent 失败: {exc}")
            return SkillResult(ok=False, output=f"run_subagent 失败: {exc}")
        summary = out.get("summary") or out.get("answer") or ""
        return SkillResult(
            ok=bool(out.get("ok")),
            output=f"[subagent {out.get('agent_id')}] {summary}",
            data=out,
        )

    def _emit_post_tool(
        self, name: str, args: dict[str, Any], result: SkillResult
    ) -> None:
        try:
            from hooks import HookEvent, emit

            event = (
                HookEvent.POST_TOOL_USE
                if result.ok
                else HookEvent.POST_TOOL_USE_FAILURE
            )
            emit(
                event,
                tool_name=name,
                payload={
                    "skill": name,
                    "arguments": args,
                    "ok": result.ok,
                    "output": (result.output or "")[:2000],
                },
            )
        except Exception:  # noqa: BLE001
            pass

    def _resolve_ask(
        self,
        guard: PermissionGuard,
        name: str,
        args: dict[str, Any],
        decision: Any,
        on_progress: ProgressHandler | None,
    ) -> bool:
        """命中 ask：有 handler 则交互；否则 headless 拒绝。放行时写入 session grant。"""
        suggestions = [
            s.to_dict() if hasattr(s, "to_dict") else {"rule": getattr(s, "rule_raw", "")}
            for s in (decision.suggestions or [])
        ]
        payload = {
            "type": "permission_ask",
            "skill": name,
            "arguments": args,
            "reason": decision.reason,
            "matched_rule": decision.matched_rule,
            "suggestions": suggestions,
        }
        try:
            from hooks import HookEvent, emit

            emit(
                HookEvent.PERMISSION_REQUEST,
                tool_name=name,
                payload=payload,
            )
        except Exception:  # noqa: BLE001
            pass
        handler = getattr(guard, "ask_handler", None)
        if handler is None:
            logger.warning(f"权限需确认但无 ask_handler: {decision.reason}")
            return False
        if on_progress is not None:
            try:
                on_progress(
                    {
                        "type": "status",
                        "phase": "permission_ask",
                        "message": f"等待授权: {name}",
                    }
                )
                on_progress(payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"permission_ask progress 失败: {exc}")
        try:
            allowed = bool(handler(payload))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"permission ask_handler 失败: {exc}")
            return False
        if allowed:
            for sug in decision.suggestions or []:
                raw = getattr(sug, "rule_raw", None) or ""
                if raw:
                    try:
                        guard.grant_session(raw)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"grant_session 失败: {exc}")
        return allowed
