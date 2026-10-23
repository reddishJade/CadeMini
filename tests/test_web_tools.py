"""验证网页转换、获取和搜索行为。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest

from cade.coding.security.policy import WorkspacePolicy
from cade.coding.tools.html_markdown import html_to_markdown
from cade.coding.tools.webfetch import WebFetchTool
from cade.coding.tools.websearch import WebSearchTool
from cade.harness.cancellation import CancellationToken
from cade.harness.executor import execute_tool
from cade.harness.session.types import ToolCallBlock


@pytest.mark.parametrize(
    ("format_name", "expected"),
    [
        ("markdown", "# Hello\n\nworld **wide**"),
        ("text", "Hello world wide"),
        (
            "html",
            "<h1>Hello</h1><p>world <strong>wide</strong></p><script>bad()</script>",
        ),
    ],
)
async def test_webfetch_formats(format_name: str, expected: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text="<h1>Hello</h1><p>world <strong>wide</strong></p><script>bad()</script>",
            headers={"content-type": "text/html"},
        )

    result = await WebFetchTool(transport=httpx.MockTransport(respond)).execute(
        ToolCallBlock(
            id="w",
            name="webfetch",
            args={"url": "http://localhost/page", "format": format_name},
        )
    )
    assert result.content == expected
    assert requests[0].url == httpx.URL("http://localhost/page")
    assert len(requests) == 1
    assert requests[0].headers["user-agent"].startswith("Cade/")


async def test_webfetch_permissions_sizes_and_cancellation(tmp_path: Path) -> None:
    called = False

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        await asyncio.Event().wait()
        return httpx.Response(200)

    tool = WebFetchTool(transport=httpx.MockTransport(respond))
    call = ToolCallBlock(id="w", name="webfetch", args={"url": "https://example.com"})
    result = await execute_tool(
        call, {"webfetch": tool}, policy=WorkspacePolicy(tmp_path, network=False)
    )
    assert result.result.is_error and not called
    token = CancellationToken()
    pending = asyncio.create_task(execute_tool(call, {"webfetch": tool}, cancel=token))
    while not called:
        await asyncio.sleep(0)
    token.cancel()
    result = await asyncio.wait_for(pending, 1)
    assert result.result.is_error
    oversized = WebFetchTool(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-length": str(5 * 1024 * 1024 + 1)}
            )
        )
    )
    assert (await oversized.execute(call)).is_error


def test_web_content_keeps_structure_and_excludes_active_content() -> None:
    converted = html_to_markdown(
        '<h1>Guide</h1><p><a href="https://example.com">Docs</a></p>'
        "<ul><li>First</li><li>Second</li></ul><pre>print(1)</pre>"
        "<script>secret()</script><style>hidden</style><iframe>embedded</iframe>"
    )
    assert "# Guide" in converted
    assert "[Docs](https://example.com)" in converted
    assert "First" in converted and "Second" in converted
    assert "```" in converted and "print(1)" in converted
    assert "secret" not in converted and "hidden" not in converted
    assert "embedded" not in converted


@pytest.mark.parametrize("encoding", ["json", "sse"])
async def test_search_uses_exa_through_mcp(
    encoding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []
    monkeypatch.setenv("EXA_API_KEY", "test-key")

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "mcp.exa.ai"
        assert request.url.params["exaApiKey"] == "test-key"
        if request.method != "POST":
            return httpx2.Response(405)
        payload = json.loads(request.content)
        requests.append(payload)
        if "id" not in payload:
            return httpx2.Response(202)
        result = (
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "exa", "version": "1"},
            }
            if payload["method"] == "initialize"
            else {
                "content": [
                    {
                        "type": "text",
                        "text": "Title: Example\nURL: https://example.com\nText: Snippet",
                    }
                ]
            }
        )
        if payload["method"] == "tools/list":
            result = {
                "tools": [{"name": "web_search_exa", "inputSchema": {"type": "object"}}]
            }
        data = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        if encoding == "sse":
            return httpx2.Response(
                200,
                text="event: message\ndata: " + json.dumps(data) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx2.Response(200, json=data)

    result = await WebSearchTool(transport=httpx2.MockTransport(respond)).execute(
        ToolCallBlock(id="s", name="websearch", args={"query": "current information"})
    )
    assert not result.is_error, result.content
    assert "https://example.com" in result.content and "Snippet" in result.content
    call = next(request for request in requests if request["method"] == "tools/call")
    assert call["params"]["name"] == "web_search_exa"
    assert call["params"]["arguments"] == {
        "query": "current information",
        "numResults": 8,
    }


async def test_search_reports_provider_failures() -> None:
    tool = WebSearchTool(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(429))
    )
    result = await tool.execute(
        ToolCallBlock(id="s", name="websearch", args={"query": "query"})
    )
    assert result.is_error and "429" in result.content


async def test_search_respects_network_policy_and_cancellation(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        started.set()
        await asyncio.Event().wait()
        return httpx2.Response(200)

    tool = WebSearchTool(transport=httpx2.MockTransport(respond))
    call = ToolCallBlock(id="s", name="websearch", args={"query": "query"})
    denied = await execute_tool(
        call, {"websearch": tool}, policy=WorkspacePolicy(tmp_path, network=False)
    )
    assert denied.result.is_error and not started.is_set()
    token = CancellationToken()
    pending = asyncio.create_task(execute_tool(call, {"websearch": tool}, cancel=token))
    await asyncio.wait_for(started.wait(), 1)
    token.cancel()
    result = await asyncio.wait_for(pending, 1)
    assert result.result.is_error
