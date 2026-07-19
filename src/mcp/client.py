"""最小 MCP stdio JSON-RPC 客户端（tools/list + tools/call）。"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any

from log import get_logger
from mcp.types import McpServerConfig, McpToolDesc

logger = get_logger("mcp.client")


class McpClientError(RuntimeError):
    pass


class McpStdioClient:
    """stdio 传输：子进程 stdin/stdout 上的 JSON-RPC 2.0。"""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._tools: list[McpToolDesc] = []

    @property
    def tools(self) -> list[McpToolDesc]:
        return list(self._tools)

    def start(self) -> None:
        if self._proc is not None:
            return
        if not self.config.command:
            raise McpClientError(f"MCP server {self.config.name}: 缺少 command")
        env = os.environ.copy()
        env.update(self.config.env)
        try:
            self._proc = subprocess.Popen(
                [self.config.command, *self.config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.config.cwd or None,
                env=env,
                bufsize=1,
            )
        except OSError as exc:
            raise McpClientError(f"启动 MCP {self.config.name} 失败: {exc}") from exc
        self._initialize()
        self._list_tools()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return result if isinstance(result, dict) else {"content": result}

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "selfagent", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def _list_tools(self) -> None:
        result = self._request("tools/list", {})
        tools_raw = (result or {}).get("tools") if isinstance(result, dict) else None
        self._tools = []
        for item in tools_raw or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            schema = item.get("inputSchema") or item.get("input_schema") or {}
            if not isinstance(schema, dict):
                schema = {}
            self._tools.append(
                McpToolDesc(
                    server=self.config.name,
                    name=name,
                    description=str(item.get("description") or ""),
                    input_schema=schema,
                )
            )

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._write(msg)

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            while True:
                msg = self._read()
                if msg is None:
                    raise McpClientError(f"MCP {self.config.name}: 连接已关闭")
                if msg.get("id") == req_id:
                    if "error" in msg:
                        err = msg["error"]
                        raise McpClientError(f"MCP {method}: {err}")
                    return msg.get("result")
                # 忽略通知与其它响应

    def _write(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpClientError("MCP 进程未启动")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        proc.stdin.write(line)
        proc.stdin.flush()

    def _read(self) -> dict[str, Any] | None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        while True:
            line = proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"MCP {self.config.name} 非 JSON 行: {line[:200]}")
                continue
            if isinstance(data, dict):
                return data


class McpManager:
    """多服务器连接与工具索引。"""

    def __init__(self) -> None:
        self._clients: dict[str, McpStdioClient] = {}
        self._tools: dict[str, McpToolDesc] = {}

    @classmethod
    def from_config(cls, section: dict[str, Any] | None = None) -> "McpManager":
        from mcp.config import load_mcp_servers

        mgr = cls()
        for server in load_mcp_servers(section):
            if not server.enabled:
                continue
            if server.transport not in {"stdio", ""}:
                logger.warning(f"MCP {server.name}: 暂不支持 transport={server.transport}")
                continue
            try:
                mgr.add_server(server)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"MCP 连接失败 [{server.name}]: {exc}")
        return mgr

    def add_server(self, config: McpServerConfig) -> list[McpToolDesc]:
        client = McpStdioClient(config)
        client.start()
        old = self._clients.pop(config.name, None)
        if old is not None:
            old.close()
            self._tools = {k: v for k, v in self._tools.items() if v.server != config.name}
        self._clients[config.name] = client
        for tool in client.tools:
            self._tools[tool.tool_name] = tool
        return client.tools

    def close(self) -> None:
        for client in list(self._clients.values()):
            client.close()
        self._clients.clear()
        self._tools.clear()

    def reload(self, section: dict[str, Any] | None = None) -> list[McpToolDesc]:
        self.close()
        other = McpManager.from_config(section)
        self._clients = other._clients
        self._tools = other._tools
        return self.list_tools()

    def list_tools(self) -> list[McpToolDesc]:
        return list(self._tools.values())

    def get_tool(self, tool_name: str) -> McpToolDesc | None:
        return self._tools.get(tool_name)

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
        from mcp.types import parse_mcp_tool_name

        parsed = parse_mcp_tool_name(tool_name)
        if parsed is None:
            return False, f"非法 MCP 工具名: {tool_name}", {}
        server, name = parsed
        client = self._clients.get(server)
        if client is None:
            return False, f"MCP 服务器未连接: {server}", {}
        try:
            result = client.call_tool(name, arguments or {})
        except Exception as exc:  # noqa: BLE001
            return False, f"MCP 调用失败: {exc}", {}
        text = _format_call_result(result)
        ok = not bool(result.get("isError")) if isinstance(result, dict) else True
        return ok, text, result if isinstance(result, dict) else {"result": result}


def _format_call_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)
    if content is not None:
        return str(content)
    return json.dumps(result, ensure_ascii=False)
