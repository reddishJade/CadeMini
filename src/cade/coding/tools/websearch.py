"""通过 Exa 搜索网页，协议收发交给 MCP SDK。"""

from __future__ import annotations

import asyncio
import os
from typing import Any, ClassVar

import httpx2 as httpx
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from cade.coding.tools.webfetch import bound_web_output
from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult


def _error_text(error: Exception) -> str:
    if isinstance(error, ExceptionGroup):
        return "; ".join(_error_text(child) for child in error.exceptions)
    return str(error)


async def _check_response(response: httpx.Response) -> None:
    if response.request.method == "POST":
        response.raise_for_status()


class WebSearchTool:
    name = "websearch"
    description = "Search the web through Exa for current information."
    manages_timeout = True
    _parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1}},
        "required": ["query"],
    }

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None = None
    ) -> ToolResult:
        url = httpx.URL("https://mcp.exa.ai/mcp")
        key = os.environ.get("EXA_API_KEY")
        if key:
            url = url.copy_add_param("exaApiKey", key)
        try:
            async with (
                asyncio.timeout(30),
                httpx.AsyncClient(
                    transport=self.transport,
                    timeout=25,
                    event_hooks={"response": [_check_response]},
                ) as http,
                Client(
                    streamable_http_client(str(url), http_client=http), mode="legacy"
                ) as client,
            ):
                result = await client.call_tool(
                    "web_search_exa", {"query": call.args["query"], "numResults": 8}
                )
            text = "\n\n".join(
                block.text
                for block in result.content
                if isinstance(block, types.TextContent)
            )
            if result.is_error:
                return ToolResult(text or "Exa search failed", is_error=True)
            return bound_web_output(
                text or "No search results found.", {"provider": "exa"}
            )
        except (httpx.HTTPError, TimeoutError, MCPError, ExceptionGroup) as exc:
            message = _error_text(exc)
            if key:
                message = message.replace(key, "[redacted]")
            return ToolResult(f"Web search failed: {message}", is_error=True)
