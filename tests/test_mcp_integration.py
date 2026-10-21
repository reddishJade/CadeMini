"""通过真实 SDK 服务验证传输、权限、取消和技能协作。"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

from cade.ai.events import FinalMessage, TextDelta, ToolCall, ToolCallEvent
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.coding.mcp.tools import McpRegistry
from cade.coding.mcp_config import McpServerConfig
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.security.rules import RuleConfig
from cade.harness.cancellation import CancellationToken
from cade.harness.executor import execute_tool, index_tools
from cade.harness.session.types import ToolCallBlock

SERVER = """
import asyncio
import os
import sys
from pathlib import Path
from mcp.server.mcpserver import MCPServer, Context

app = MCPServer("acceptance")

@app.tool()
async def echo(text: str, ctx: Context) -> dict[str, str]:
    await ctx.report_progress(1, 1, "finished")
    return {"text": text}

@app.tool()
async def slow() -> str:
    Path("started").write_text("started")
    await asyncio.sleep(1)
    Path("effect").write_text("unexpected")
    return "done"

@app.tool()
def environment() -> dict[str, str]:
    return {"ambient": os.getenv("CADE_AMBIENT_SECRET", "absent"),
            "explicit": os.getenv("CADE_EXPLICIT_SECRET", "absent")}

@app.resource("test://guide")
def guide() -> str:
    return "resource evidence"

@app.prompt()
def review(subject: str) -> str:
    return "Review " + subject

if len(sys.argv) > 1:
    app.run("streamable-http", port=int(sys.argv[1]), stateless_http=True)
else:
    app.run()
"""


@pytest.fixture
def server_script(tmp_path: Path) -> Path:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    return script


@pytest.fixture
def http_server(server_script: Path) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (server_script.parent / "http.log").open("w") as stderr:
        process = subprocess.Popen(
            [sys.executable, str(server_script), str(port)],
            cwd=server_script.parent,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    pytest.fail("HTTP MCP server exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        pytest.fail("HTTP MCP server did not become ready")
                    time.sleep(0.02)
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            process.terminate()
            process.wait(timeout=10)


@pytest.mark.parametrize("protocol", ["legacy", "auto"])
async def test_sdk_stdio_context_and_results(
    tmp_path: Path, server_script: Path, protocol: str
) -> None:
    config = McpServerConfig(
        command=sys.executable,
        args=[str(server_script)],
        protocol=protocol,
    )
    registry = McpRegistry({"acceptance": config}, tmp_path)
    try:
        tools = index_tools(await registry.refresh())
        assert registry.errors == ()
        connection = registry.connections["acceptance"]
        assert connection.protocol_version == (
            "2025-11-25" if protocol == "legacy" else "2026-07-28"
        )
        echo = tools["mcp__acceptance__echo"]
        result = await echo.execute(
            ToolCallBlock(id="echo", name=echo.name, args={"text": "evidence"})
        )
        assert not result.is_error
        assert "evidence" in result.content
        assert result.metadata["validation"] == "valid"
        assert result.metadata["progress"][0]["message"] == "finished"
        resource = tools["mcp__acceptance__resources_read"]
        result = await resource.execute(
            ToolCallBlock(id="r", name=resource.name, args={"uri": "test://guide"})
        )
        assert "resource evidence" in result.content
        prompt = tools["mcp__acceptance__prompts_get"]
        result = await prompt.execute(
            ToolCallBlock(
                id="p",
                name=prompt.name,
                args={"name": "review", "arguments": {"subject": "bug"}},
            )
        )
        assert "Review bug" in result.content
    finally:
        registry.close()


async def test_streamable_http(tmp_path: Path, http_server: str) -> None:
    registry = McpRegistry(
        {
            "http": McpServerConfig(
                transport="streamable-http",
                url=http_server,
            )
        },
        tmp_path,
    )
    try:
        tools = index_tools(await registry.refresh())
        assert registry.errors == ()
        tool = tools["mcp__http__echo"]
        result = await tool.execute(
            ToolCallBlock(
                id="http",
                name=tool.name,
                args={"text": "HTTP evidence"},
            )
        )
        assert not result.is_error
        assert "HTTP evidence" in result.content
    finally:
        registry.close()


async def test_cancellation_stops_effect_and_reconnects(
    tmp_path: Path,
    server_script: Path,
) -> None:
    registry = McpRegistry(
        {
            "test": McpServerConfig(
                command=sys.executable,
                args=[str(server_script)],
            )
        },
        tmp_path,
    )
    try:
        tools = index_tools(await registry.refresh())
        slow = tools["mcp__test__slow"]
        token = CancellationToken()
        task = asyncio.create_task(
            slow.execute(
                ToolCallBlock(id="slow", name=slow.name, args={}),
                token,
            )
        )
        async with asyncio.timeout(5):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(0.01)
        token.cancel()
        result = await asyncio.wait_for(task, timeout=5)
        assert result.is_error
        assert not (tmp_path / "effect").exists()
        first = registry.connections["test"]
        await registry.refresh()
        assert registry.connections["test"] is not first
        assert registry.connections["test"].connected
    finally:
        registry.close()


async def test_schema_and_permissions_prevent_remote_effect(
    tmp_path: Path,
    server_script: Path,
) -> None:
    registry = McpRegistry(
        {
            "test": McpServerConfig(
                command=sys.executable,
                args=[str(server_script)],
            )
        },
        tmp_path,
    )
    try:
        tools = index_tools(await registry.refresh())
        call = ToolCallBlock(id="denied", name="mcp__test__slow", args={})
        denied = await execute_tool(call, tools, policy=WorkspacePolicy(tmp_path))
        assert denied.result.is_error
        assert not (tmp_path / "started").exists()
        invalid = await execute_tool(
            ToolCallBlock(id="invalid", name="mcp__test__echo", args={}),
            tools,
        )
        assert invalid.result.is_error
        assert "schema" in invalid.result.content
    finally:
        registry.close()


async def test_ambient_credentials_are_not_inherited(
    tmp_path: Path,
    server_script: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADE_AMBIENT_SECRET", "ambient-test-secret")
    monkeypatch.setenv("CADE_SOURCE_SECRET", "explicit-test-secret")
    registry = McpRegistry(
        {
            "test": McpServerConfig(
                command=sys.executable,
                args=[str(server_script)],
                env_from={"CADE_EXPLICIT_SECRET": "CADE_SOURCE_SECRET"},
            )
        },
        tmp_path,
    )
    try:
        tools = index_tools(await registry.refresh())
        tool = tools["mcp__test__environment"]
        result = await tool.execute(ToolCallBlock(id="env", name=tool.name, args={}))
        assert "absent" in result.content
        assert "ambient-test-secret" not in result.content
        assert "explicit-test-secret" not in str(result)
        assert "[redacted]" in result.content
    finally:
        registry.close()


@pytest.mark.parametrize(
    "secret", ['quoted-"secret\\with\nnewline', "1", '密钥"\\文本']
)
async def test_redaction_preserves_protocol_values_and_escaped_strings(
    tmp_path: Path,
    server_script: Path,
    monkeypatch: pytest.MonkeyPatch,
    secret: str,
) -> None:
    monkeypatch.setenv("CADE_SOURCE_SECRET", secret)
    registry = McpRegistry(
        {
            "test": McpServerConfig(
                command=sys.executable,
                args=[str(server_script)],
                env_from={"CADE_EXPLICIT_SECRET": "CADE_SOURCE_SECRET"},
            )
        },
        tmp_path,
    )
    try:
        tools = index_tools(await registry.refresh())
        tool = tools["mcp__test__echo"]
        result = await tool.execute(
            ToolCallBlock(
                id="escaped",
                name=tool.name,
                args={"text": secret},
            )
        )
        assert not result.is_error
        assert (
            result.metadata["mcp_result"]["structuredContent"]["text"] == "[redacted]"
        )
        assert result.metadata["progress"][0]["progress"] == 1
        assert result.metadata["validation"] == "valid"
        assert "[redacted]" in result.content
        assert "quoted-" not in result.content
    finally:
        registry.close()


def test_skill_guides_mcp_calls_through_application_loop(
    tmp_path: Path,
    server_script: Path,
) -> None:
    skill = tmp_path / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: review\ndescription: Review bugs using MCP\n---\n"
        "Call mcp__test__echo with text workflow-evidence.",
        encoding="utf-8",
    )

    class WorkflowProvider:
        model = "test"
        transport = "test"
        context_window = 100_000

        def __init__(self) -> None:
            self.requests = []

        async def stream(self, messages, tools, options=None):
            self.requests.append(messages)
            index = len(self.requests)
            if index == 1:
                yield ToolCallEvent(
                    [ToolCall(id="s", name="skill", input={"name": "review"})]
                )
                yield FinalMessage("", "tool_use")
            elif index == 2:
                assert "workflow-evidence" in str(messages)
                yield ToolCallEvent(
                    [
                        ToolCall(
                            id="m",
                            name="mcp__test__echo",
                            input={"text": "workflow-evidence"},
                        )
                    ]
                )
                yield FinalMessage("", "tool_use")
            else:
                assert "workflow-evidence" in str(messages)
                yield TextDelta("Verified")
                yield FinalMessage("Verified", "end_turn")

    provider = WorkflowProvider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(
                trust_project_skills=True,
                memory=False,
                note=False,
                instructions=False,
                mcp_servers={
                    "test": McpServerConfig(
                        command=sys.executable, args=[str(server_script)]
                    )
                },
                rules=[RuleConfig(tool="mcp__test__echo", decision="allow")],
            ),
            resources=resources,
            provider=provider,
        ).session
        result = asyncio.run(session.run_task("Review a bug", lambda event: None))
        assert result.termination == "completed"
        assert result.answer == "Verified"
        results = [
            entry for entry in session.log.branch() if entry.type == "tool_result"
        ]
        assert len(results) == 2
        metadata = results[-1].data["artifact"]["tool_metadata"]
        assert metadata["protocol_version"] == "2026-07-28"
        assert metadata["mcp_tool"] == "echo"
