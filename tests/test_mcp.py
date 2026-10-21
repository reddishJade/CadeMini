"""Cade 持久 MCP 客户端的回归测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from cade.coding.mcp.results import convert_mcp_tool_result
from cade.coding.mcp.tools import McpRegistry, McpTool
from cade.coding.mcp_config import McpServerConfig
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.security.rules import RuleConfig
from cade.harness.session.types import ToolCallBlock

_SERVER = """\
import json
import sys

count = 0

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
            "capabilities": {"tools": {}},
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
                },
                {
                    "name": "counter",
                    "description": "Count calls",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
        }
    elif method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if params.get("name") == "counter":
            count += 1
            text = "count:" + str(count)
        else:
            text = "echo:" + str(arguments.get("text", ""))
        result = {"content": [{"type": "text", "text": text}], "isError": False}
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
        protocol="legacy",
    )


def _tool(tools: tuple[McpTool, ...], name: str) -> McpTool:
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


def test_discover_and_call_mcp_tool(tmp_path: Path) -> None:
    registry = McpRegistry({"fake": _server(tmp_path)}, tmp_path)
    tools = asyncio.run(registry.refresh())
    errors = registry.errors
    try:
        assert errors == ()
        assert len(tools) == 2
        tool = _tool(tools, "mcp__fake__echo")
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
    finally:
        for tool in tools:
            tool.close()


def test_mcp_connection_persists_between_calls(tmp_path: Path) -> None:
    registry = McpRegistry({"fake": _server(tmp_path)}, tmp_path)
    tools = asyncio.run(registry.refresh())
    errors = registry.errors
    try:
        assert errors == ()
        counter = _tool(tools, "mcp__fake__counter")

        first = asyncio.run(
            counter.execute(ToolCallBlock(id="m1", name=counter.name, args={}))
        )
        second = asyncio.run(
            counter.execute(ToolCallBlock(id="m2", name=counter.name, args={}))
        )
        assert first.content == "count:1"
        assert second.content == "count:2"
    finally:
        for tool in tools:
            tool.close()


def test_mcp_discovery_records_start_errors(tmp_path: Path) -> None:
    config = McpServerConfig(command="definitely-not-a-command")

    registry = McpRegistry({"broken": config}, tmp_path)
    tools = asyncio.run(registry.refresh())
    errors = registry.errors

    assert tools == ()
    assert errors
    assert "broken" in errors[0]


def test_mcp_policy_defaults_and_rules(tmp_path: Path) -> None:
    call = ToolCallBlock(id="m1", name="mcp__fake__echo", args={"text": "hi"})

    assert WorkspacePolicy(tmp_path).check(call).decision == "ask"
    assert WorkspacePolicy(tmp_path, mode="plan").check(call).decision == "deny"
    assert WorkspacePolicy(tmp_path, read_only=True).check(call).decision == "deny"
    rules = [
        RuleConfig(tool="mcp__fake__echo", decision="allow"),
    ]
    assert WorkspacePolicy(tmp_path, rules=rules).check(call).decision == "allow"


async def test_unsupported_version_is_rejected(tmp_path: Path) -> None:
    config = _server(tmp_path)
    script = tmp_path / "server.py"
    script.write_text(_SERVER.replace("2024-11-05", "2099-01-01"), encoding="utf-8")
    registry = McpRegistry({"bad": config}, tmp_path)
    try:
        assert await registry.refresh() == ()
        assert "Unsupported protocol version" in str(registry.errors)
    finally:
        registry.close()


async def test_pagination_and_server_ping_do_not_lose_results(tmp_path: Path) -> None:
    config = _server(tmp_path)
    script = tmp_path / "server.py"
    text = _SERVER.replace(
        '    method = message.get("method")',
        '    method = message.get("method")\n    if method is None: continue',
    )
    text = text.replace(
        '    elif method == "tools/call":',
        """        if message.get("params", {}).get("cursor") is None:
            result["tools"] = result["tools"][:1]
            result["nextCursor"] = "second"
        else:
            result["tools"] = result["tools"][1:]
    elif method == "tools/call":
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "method": "ping"}), flush=True)""",
    )
    script.write_text(text, encoding="utf-8")
    registry = McpRegistry({"test": config}, tmp_path)
    try:
        tools = await registry.refresh()
        assert registry.errors == ()
        assert len(tools) == 2
        tool = _tool(tools, "mcp__test__echo")
        result = await tool.execute(
            ToolCallBlock(
                id="ping",
                name=tool.name,
                args={"text": "actual result"},
            )
        )
        assert result.content == "echo:actual result"
    finally:
        registry.close()


async def test_list_changed_refreshes_live_tools(tmp_path: Path) -> None:
    config = _server(tmp_path)
    script = tmp_path / "server.py"
    text = _SERVER.replace(
        '    elif method == "tools/call":',
        """        if count:
            result["tools"].append({"name": "added", "inputSchema": {"type": "object"}})
    elif method == "tools/call":""",
    ).replace(
        "            count += 1",
        """            count += 1
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}), flush=True)""",
    )
    script.write_text(text, encoding="utf-8")
    registry = McpRegistry({"test": config}, tmp_path)
    try:
        tool = _tool(await registry.refresh(), "mcp__test__counter")
        await tool.execute(ToolCallBlock(id="counter", name=tool.name, args={}))
        tools = await registry.refresh()
        assert "mcp__test__added" in {tool.name for tool in tools}
    finally:
        registry.close()


@pytest.mark.parametrize("names", [("a-b", "a_b"), ("x" * 100, "x" * 101)])
async def test_ambiguous_tool_names_are_not_exposed(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    config = _server(tmp_path)
    script = tmp_path / "server.py"
    script.write_text(
        _SERVER.replace('"name": "echo"', f'"name": "{names[0]}"').replace(
            '"name": "counter"', f'"name": "{names[1]}"'
        ),
        encoding="utf-8",
    )
    registry = McpRegistry({"test": config}, tmp_path)
    try:
        assert await registry.refresh() == ()
        assert registry.errors
    finally:
        registry.close()


def test_structured_result_validation_preserves_other_content() -> None:
    result = convert_mcp_tool_result(
        {
            "content": [
                {"type": "text", "text": "explanation"},
                {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"},
            ],
            "structuredContent": {"count": "wrong"},
        },
        {"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    assert result.is_error
    assert "explanation" in result.content
    assert "aGVsbG8=" in result.content
    assert result.metadata["validation"] == "invalid"


async def test_startup_stderr_is_reported_without_credentials(tmp_path: Path) -> None:
    config = _server(tmp_path).model_copy(update={"env": {"TOKEN": "test-secret"}})
    script = tmp_path / "server.py"
    script.write_text(
        "import os, sys\nprint('failed: ' + os.environ['TOKEN'], file=sys.stderr, flush=True)\nsys.exit(1)",
        encoding="utf-8",
    )
    registry = McpRegistry({"broken": config}, tmp_path)
    try:
        assert await registry.refresh() == ()
        assert "failed:" in str(registry.errors)
        assert "test-secret" not in str(registry.errors)
        assert "[redacted]" in str(registry.errors)
    finally:
        registry.close()


async def test_request_timeout_closes_connection_without_retry(tmp_path: Path) -> None:
    config = _server(tmp_path).model_copy(update={"timeout_ms": 500})
    script = tmp_path / "server.py"
    script.write_text(
        _SERVER.replace(
            '    elif method == "tools/call":',
            """    elif method == "tools/call":
        import time
        from pathlib import Path
        count_file = Path("timeout-count")
        count_file.write_text(str(int(count_file.read_text()) + 1) if count_file.exists() else "1")
        time.sleep(2)""",
        ),
        encoding="utf-8",
    )
    registry = McpRegistry({"test": config}, tmp_path)
    try:
        tools = await registry.refresh()
        assert registry.errors == ()
        tool = _tool(tools, "mcp__test__counter")
        with pytest.raises((TimeoutError, RuntimeError)):
            await tool.execute(ToolCallBlock(id="timeout", name=tool.name, args={}))
        assert not registry.connections["test"].connected
        assert (tmp_path / "timeout-count").read_text() == "1"
    finally:
        registry.close()
