"""从 config.yaml 读取 mcp.servers。"""

from __future__ import annotations

from typing import Any

import config as cfg
from mcp.types import McpServerConfig


def load_mcp_servers(section: dict[str, Any] | None = None) -> list[McpServerConfig]:
    mcp_cfg = section if section is not None else (cfg.get_section("mcp", {}) or {})
    raw_servers = mcp_cfg.get("servers") or {}
    out: list[McpServerConfig] = []
    if isinstance(raw_servers, dict):
        items = raw_servers.items()
    elif isinstance(raw_servers, list):
        items = []
        for item in raw_servers:
            if isinstance(item, dict) and item.get("name"):
                items.append((str(item["name"]), item))
    else:
        return out

    for name, body in items:
        if not isinstance(body, dict):
            continue
        enabled = body.get("enabled", True)
        transport = str(body.get("transport") or body.get("type") or "stdio").strip().lower()
        command = str(body.get("command") or "").strip()
        args = [str(a) for a in (body.get("args") or [])]
        env = {str(k): str(v) for k, v in (body.get("env") or {}).items()}
        cwd = body.get("cwd")
        url = str(body.get("url") or "").strip()
        out.append(
            McpServerConfig(
                name=str(name).strip(),
                command=command,
                args=args,
                env=env,
                cwd=str(cwd) if cwd else None,
                transport=transport,
                url=url,
                enabled=bool(enabled),
            )
        )
    return out
