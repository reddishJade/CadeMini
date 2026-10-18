"""持久 stdio MCP 客户端：每个 server 一个长连接，按需调用工具。"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from cade_mini.app.config import McpServerConfig
from cade_mini.core.loop import CancellationToken, ToolResult
from cade_mini.core.types import JsonValue, ToolCallBlock

_PROTOCOL_VERSION = "2024-11-05"
_MAX_TOOL_NAME = 96


class McpError(RuntimeError):
    """MCP server 通信失败。"""


class McpConnection:
    """一个长期存活的 MCP 子进程；请求串行化，响应按 id 匹配。"""

    def __init__(self, config: McpServerConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace.resolve()
        self._lock = threading.Lock()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._next_id = 0
        self._closed = False
        env = os.environ.copy()
        if config.env:
            env.update(config.env)
        cwd = self.workspace / config.cwd if config.cwd else self.workspace
        try:
            self.process = subprocess.Popen(
                [config.command, *config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"cannot start MCP server: {exc}") from exc
        if self.process.stdin is None or self.process.stdout is None:
            self.process.kill()
            raise McpError("MCP server has no stdio pipes")
        self._reader = threading.Thread(
            target=self._read_loop,
            name="cade-mini-mcp",
            daemon=True,
        )
        self._reader.start()
        atexit.register(self.close)
        self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cade-mini", "version": "1.0"},
            },
        )
        self._notify("notifications/initialized")

    def list_tools(self) -> list[Any]:
        result = self._request("tools/list", {})
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call(self, name: str, arguments: dict[str, JsonValue]) -> dict[str, Any]:
        return self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            try:
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _read_loop(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            return
        for line in stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._queue.put(message)
        self._queue.put(None)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise McpError("MCP connection is closed")
            self._next_id += 1
            request_id = self._next_id
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            while True:
                try:
                    message = self._queue.get(timeout=self.config.timeout_ms / 1000)
                except queue.Empty as exc:
                    self.close()
                    raise McpError(
                        f"MCP server timed out after {self.config.timeout_ms}ms"
                    ) from exc
                if message is None:
                    raise McpError("MCP server closed stdout")
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise McpError(f"MCP error: {message['error']}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def _notify(self, method: str) -> None:
        with self._lock:
            if self._closed:
                raise McpError("MCP connection is closed")
            self._write({"jsonrpc": "2.0", "method": method})

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise McpError("MCP server has no stdin")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()


class McpTool:
    """把一个远端 MCP 工具暴露成 Mini 工具。"""

    def __init__(
        self,
        *,
        server: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        connection: McpConnection,
    ) -> None:
        self.server = server
        self.remote_name = remote_name
        self.name = f"mcp__{_safe(server)}__{_safe(remote_name)}"[:_MAX_TOOL_NAME]
        self.description = description or remote_name
        self.parameters = parameters
        self.connection = connection

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        del signal
        result = await asyncio.to_thread(
            self.connection.call,
            self.remote_name,
            call.args,
        )
        parts: list[str] = []
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
        text = "\n".join(part for part in parts if part).strip()
        if not text:
            text = json.dumps(result, ensure_ascii=False, sort_keys=True)[:4_000]
        return ToolResult(
            content=text,
            is_error=bool(result.get("isError")),
            metadata={
                "mcp_server": self.server,
                "mcp_tool": self.remote_name,
            },
        )

    def close(self) -> None:
        self.connection.close()


async def discover_mcp_tools(
    servers: dict[str, McpServerConfig],
    workspace: Path,
) -> tuple[tuple[McpTool, ...], tuple[str, ...]]:
    """逐个连接 server 并发现工具；失败记录错误而不阻断启动。"""
    tools: list[McpTool] = []
    errors: list[str] = []
    for server_name, config in servers.items():
        connection: McpConnection | None = None
        try:
            connection = await asyncio.to_thread(
                McpConnection,
                config,
                workspace,
            )
            raw_tools = await asyncio.to_thread(connection.list_tools)
        except (McpError, OSError) as exc:
            if connection is not None:
                connection.close()
            errors.append(f"{server_name}: {exc}")
            continue
        created = False
        for item in raw_tools:
            if not isinstance(item, dict):
                continue
            remote_name = item.get("name")
            if not isinstance(remote_name, str) or not remote_name:
                continue
            schema = item.get("inputSchema")
            parameters = (
                schema
                if isinstance(schema, dict)
                else {"type": "object", "properties": {}}
            )
            description = item.get("description")
            tools.append(
                McpTool(
                    server=server_name,
                    remote_name=remote_name,
                    description=description if isinstance(description, str) else "",
                    parameters=parameters,
                    connection=connection,
                )
            )
            created = True
        if not created:
            connection.close()
    return tuple(tools), tuple(errors)


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value) or "tool"
