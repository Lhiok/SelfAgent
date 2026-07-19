"""Hook runners：command / http / prompt。"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import httpx

from hooks.types import HookDefinition, HookResult
from log import get_logger

logger = get_logger("hooks.runners")


def run_hook(
    definition: HookDefinition,
    payload: dict[str, Any],
) -> HookResult:
    kind = (definition.runner or "command").strip().lower()
    if kind == "command":
        return run_command(definition, payload)
    if kind == "http":
        return run_http(definition, payload)
    if kind == "prompt":
        return run_prompt(definition, payload)
    logger.warning(f"未知 hook runner: {kind}")
    return HookResult.allow(f"unknown runner {kind}")


def run_command(definition: HookDefinition, payload: dict[str, Any]) -> HookResult:
    completed = _exec(definition, payload)
    if completed is None:
        return HookResult(blocked=False, reason="hook error", ok=False)
    if isinstance(completed, HookResult):
        return completed
    # exit 2 = block（对齐 CC）
    if completed.returncode == 2:
        msg = (completed.stderr or completed.stdout or "blocked by hook").strip()
        return HookResult.block(msg[:2000])
    if completed.returncode != 0:
        msg = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        return HookResult(blocked=False, reason=msg[:2000], ok=False)
    return HookResult.allow((completed.stdout or "").strip()[:500])


def run_http(definition: HookDefinition, payload: dict[str, Any]) -> HookResult:
    url = (definition.url or "").strip()
    if not url:
        return HookResult.allow("empty url")
    try:
        with httpx.Client(timeout=max(1.0, float(definition.timeout or 30))) as client:
            resp = client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001
        return HookResult(blocked=False, reason=f"http hook error: {exc}", ok=False)
    if resp.status_code in {403, 409}:
        return HookResult.block((resp.text or "blocked by http hook")[:2000])
    if resp.status_code >= 400:
        return HookResult(
            blocked=False,
            reason=f"http {resp.status_code}: {resp.text[:500]}",
            ok=False,
        )
    return HookResult.allow(f"http {resp.status_code}")


def run_prompt(definition: HookDefinition, payload: dict[str, Any]) -> HookResult:
    """执行 command，将 stdout 作为附加上下文。"""
    completed = _exec(definition, payload)
    if completed is None:
        return HookResult(blocked=False, reason="prompt hook error", ok=False)
    if isinstance(completed, HookResult):
        return completed
    if completed.returncode == 2:
        return HookResult.block((completed.stderr or completed.stdout or "blocked")[:2000])
    text = (completed.stdout or "").strip()
    return HookResult(
        blocked=False,
        reason="prompt",
        extra_context=text,
        ok=completed.returncode == 0,
        data={"inject_as": definition.inject_as},
    )


def _exec(
    definition: HookDefinition, payload: dict[str, Any]
) -> subprocess.CompletedProcess[str] | HookResult | None:
    cmd = (definition.command or "").strip()
    if not cmd:
        return HookResult.allow("empty command")
    env = os.environ.copy()
    env.update(definition.env)
    env["SELFAGENT_HOOK_EVENT"] = str(payload.get("event") or "")
    env["SELFAGENT_HOOK_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
    try:
        return subprocess.run(
            cmd,
            shell=True,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(definition.timeout or 30)),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return HookResult(blocked=False, reason="hook timeout", ok=False)
    except Exception as exc:  # noqa: BLE001
        return HookResult(blocked=False, reason=f"hook error: {exc}", ok=False)
