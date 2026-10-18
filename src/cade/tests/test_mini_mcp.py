"""Cade Mini 最小 MCP 客户端的回归测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from cade.mini import (
    McpServerConfig,
    RuleConfig,
    ToolCallBlock,
    WorkspacePolicy,
    discover_mcp_tools,
)

_SERVER = """\
import json
import sys

for line in sys.stdin:
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        result = {
            "content": [
                {"type": "text", "text": "echo:" + str(arguments.get("text", ""))}
            ],
            "isError": False,
        }
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


def _server(tmp_path: Path) -> McpServerConfig:
    script = tmp_path / "server.py"
    script.write_text(_SERVER, encoding="utf-8")
    return McpServerConfig(
        command=sys.executable,
        args=[str(script)],
        timeout_ms=10_000,
    )


def test_discover_and_call_mcp_tool(tmp_path: Path) -> None:
    tools, errors = asyncio.run(
        discover_mcp_tools({"fake": _server(tmp_path)}, tmp_path)
    )

    assert errors == ()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "mcp__fake__echo"
    assert "text" in tool.parameters["properties"]

    result = asyncio.run(
        tool.execute(
            ToolCallBlock(
                id="m1",
                name=tool.name,
                args={"text": "hi"},
            )
        )
    )
    assert result.content == "echo:hi"
    assert result.is_error is False


def test_mcp_discovery_records_start_errors(tmp_path: Path) -> None:
    config = McpServerConfig(command="definitely-not-a-command")

    tools, errors = asyncio.run(discover_mcp_tools({"broken": config}, tmp_path))

    assert tools == ()
    assert errors
    assert "broken" in errors[0]


def test_mcp_policy_defaults_and_rules(tmp_path: Path) -> None:
    call = ToolCallBlock(id="m1", name="mcp__fake__echo", args={"text": "hi"})

    assert WorkspacePolicy(tmp_path).check(call) == "ask"
    assert WorkspacePolicy(tmp_path, mode="plan").check(call) == "deny"
    assert WorkspacePolicy(tmp_path, read_only=True).check(call) == "deny"
    rules = [
        RuleConfig(tool="mcp__fake__echo", decision="allow"),
    ]
    assert WorkspacePolicy(tmp_path, rules=rules).check(call) == "allow"
