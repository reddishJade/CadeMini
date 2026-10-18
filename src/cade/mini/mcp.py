"""最小 stdio MCP 客户端：发现工具并按需调用。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import McpServerConfig
from .loop import CancellationToken, ToolResult
from .types import JsonValue, ToolCallBlock

_PROTOCOL_VERSION = "2024-11-05"
_MAX_TOOL_NAME = 96


class McpError(RuntimeError):
    """MCP server 通信失败。"""


class McpTool:
    """把一个远端 MCP 工具暴露成 Mini 工具。"""

    def __init__(
        self,
        *,
        server: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        config: McpServerConfig,
        workspace: Path,
    ) -> None:
        self.server = server
        self.remote_name = remote_name
        self.name = f"mcp__{_safe(server)}__{_safe(remote_name)}"[:_MAX_TOOL_NAME]
        self.description = description or remote_name
        self.parameters = parameters
        self.config = config
        self.workspace = workspace.resolve()

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        del signal
        results = await _run_server(
            self.config,
            self.workspace,
            _call_requests(self.remote_name, call.args),
        )
        result = results[-1]
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


async def discover_mcp_tools(
    servers: dict[str, McpServerConfig],
    workspace: Path,
) -> tuple[tuple[McpTool, ...], tuple[str, ...]]:
    """逐个连接 server 并发现工具；失败记录错误而不阻断启动。"""
    tools: list[McpTool] = []
    errors: list[str] = []
    for server_name, config in servers.items():
        try:
            results = await _run_server(
                config,
                workspace,
                _discovery_requests(),
            )
        except (McpError, OSError) as exc:
            errors.append(f"{server_name}: {exc}")
            continue
        raw_tools = results[-1].get("tools", []) if results else []
        if not isinstance(raw_tools, list):
            continue
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
                    config=config,
                    workspace=workspace,
                )
            )
    return tuple(tools), tuple(errors)


async def _run_server(
    config: McpServerConfig,
    workspace: Path,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    env = os.environ.copy()
    if config.env:
        env.update(config.env)
    cwd = workspace / config.cwd if config.cwd else workspace
    try:
        process = await asyncio.create_subprocess_exec(
            config.command,
            *config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        raise McpError(f"cannot start MCP server: {exc}") from exc
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise McpError("MCP server has no stdio pipes")
    try:
        async with asyncio.timeout(config.timeout_ms / 1000):
            results: list[dict[str, Any]] = []
            for request in requests:
                stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
                await stdin.drain()
                if "id" in request:
                    results.append(await _read_result(stdout, request["id"]))
            return results
    except TimeoutError as exc:
        process.kill()
        raise McpError(f"MCP server timed out after {config.timeout_ms}ms") from exc
    finally:
        try:
            stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def _read_result(
    stdout: asyncio.StreamReader,
    request_id: object,
) -> dict[str, Any]:
    while True:
        line = await stdout.readline()
        if not line:
            raise McpError("MCP server closed stdout")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            raise McpError(f"MCP error: {message['error']}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}


def _discovery_requests() -> list[dict[str, Any]]:
    return [
        _initialize_request(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]


def _call_requests(
    name: str,
    arguments: dict[str, JsonValue],
) -> list[dict[str, Any]]:
    return [
        _initialize_request(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    ]


def _initialize_request() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "cade-mini", "version": "1.0"},
        },
    }


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value) or "tool"
