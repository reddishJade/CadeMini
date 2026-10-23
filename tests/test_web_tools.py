"""验证网页转换、获取和搜索行为。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cade.coding.security.policy import WorkspacePolicy, filter_tools
from cade.coding.tools.html_markdown import html_to_markdown
from cade.coding.tools.question import Question
from cade.coding.tools.webfetch import WebFetchTool
from cade.coding.tools.websearch import WebSearchTool
from cade.harness.cancellation import CancellationToken
from cade.harness.executor import execute_tool
from cade.harness.session.types import ToolCallBlock

_CORPUS = json.loads(
    (Path(__file__).parent / "fixtures/web_tools.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _CORPUS["html"])
def test_html_reference_results(case: dict[str, str]) -> None:
    assert html_to_markdown(case["html"]) == case["expected"]


@pytest.mark.parametrize(
    ("format_name", "expected"),
    [
        ("markdown", "# Hello\n\nworld **wide**"),
        ("text", "Helloworld wide"),
        (
            "html",
            "<h1>Hello</h1><p>world <strong>wide</strong></p><script>bad()</script>",
        ),
    ],
)
async def test_webfetch_formats_and_cloudflare_retry(
    format_name: str, expected: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(403, headers={"cf-mitigated": "challenge"})
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
    assert requests[1].headers["user-agent"] == "opencode"


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


@pytest.mark.parametrize(
    "provider", ["exa", "firecrawl", "parallel", "tavily", "tinyfish"]
)
async def test_search_provider_payloads(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []
    monkeypatch.delenv(provider.upper() + "_API_KEY", raising=False)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if provider == "tavily":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Example",
                            "content": "Snippet",
                        }
                    ]
                },
            )
        text = "Title: Example\nURL: https://example.com\nText: Snippet"
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if provider == "parallel":
            result["structuredContent"] = {
                "results": [
                    {
                        "url": "https://example.com",
                        "title": "Example",
                        "excerpts": ["Snippet"],
                    }
                ]
            }
        elif provider in {"firecrawl", "tinyfish"}:
            result["content"][0]["text"] = json.dumps(
                {
                    "data": {
                        "web": [
                            {
                                "url": "https://example.com",
                                "title": "Example",
                                "description": "Snippet",
                            }
                        ]
                    }
                }
                if provider == "firecrawl"
                else {
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Example",
                            "snippet": "Snippet",
                        }
                    ]
                }
            )
        return httpx.Response(
            200, text="data: " + json.dumps({"result": result}) + "\n\n"
        )

    tool = WebSearchTool(selection=provider, transport=httpx.MockTransport(respond))
    result = await tool.execute(
        ToolCallBlock(id="w", name="websearch", args={"query": "current information"})
    )
    assert result.content == "## [Example](https://example.com)\n\nSnippet"
    assert not result.is_error
    if provider == "exa":
        assert requests[0]["params"] == {
            "name": "web_search_exa",
            "arguments": {"query": "current information", "numResults": 8},
        }
    elif provider == "tavily":
        assert requests[0] == {
            "query": "current information",
            "search_depth": "basic",
            "chunks_per_source": 3,
            "max_results": 8,
        }


async def test_search_selection_disable_and_persistence() -> None:
    saved: list[Any] = []

    async def answer(questions: tuple[Question, ...]) -> list[list[str]]:
        return [["Disable web search"]]

    tool = WebSearchTool(answer=answer, save_selection=saved.append)
    result = await tool.execute(
        ToolCallBlock(id="s", name="websearch", args={"query": "query"})
    )
    assert result.is_error
    assert saved == [False]
    assert not filter_tools((tool,), "build")
