"""管理搜索服务的选择、请求和模型输出。"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Literal, cast

import httpx

from cade.coding.tools.question import AnswerHandler, Question
from cade.coding.tools.webfetch import bound_web_output, collect_body
from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult, current_tool_context

type SearchSelection = (
    Literal["exa", "firecrawl", "parallel", "tavily", "tinyfish", "random", False]
    | None
)
PROVIDERS = {
    "exa": "Exa",
    "firecrawl": "Firecrawl",
    "parallel": "Parallel",
    "tavily": "Tavily",
    "tinyfish": "TinyFish",
}
_ENDPOINTS = {
    "exa": "https://mcp.exa.ai/mcp",
    "firecrawl": "https://mcp.firecrawl.dev/v2/mcp",
    "parallel": "https://search.parallel.ai/mcp",
    "tinyfish": "https://agent.tinyfish.ai/mcp",
}


def _published(value: str | None) -> str | None:
    if not value or value == "N/A":
        return None
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            return None
    return (
        date.replace(tzinfo=date.tzinfo or UTC)
        .astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_mcp(body: str) -> dict[str, Any] | None:
    for payload in [
        body.strip(),
        *(line[6:] for line in body.split("\n") if line.startswith("data: ")),
    ]:
        if not payload.startswith("{"):
            continue
        decoded = json.loads(payload)
        if isinstance(decoded, dict) and isinstance(decoded.get("result"), dict):
            return decoded["result"]
        raise ValueError("Invalid MCP search response")
    return None


class WebSearchTool:
    name = "websearch"
    _parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Websearch query"}},
        "required": ["query"],
    }
    manages_timeout = True

    def __init__(
        self,
        *,
        selection: SearchSelection = None,
        answer: AnswerHandler | None = None,
        save_selection: Callable[[SearchSelection], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.selection = selection
        self.answer = answer
        self.save_selection = save_selection
        self.transport = transport
        self.preferred: dict[str, str] = {}
        self.cooldowns: dict[str, tuple[float, httpx.HTTPStatusError]] = {}
        self._selection_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.selection is not False

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def description(self) -> str:
        return f"Search the web using the user's selected search integration. Use this for current information beyond knowledge cutoff.\n\nThe current year is {datetime.now().year}. Use this year when searching for recent information or current events."

    async def choose(self) -> None:
        async with self._selection_lock:
            if self.selection is not None:
                return
            if self.answer is None:
                raise ValueError("Web search cancelled")
            async with asyncio.timeout(60):
                allow_label = "Allow search via " + ", ".join(PROVIDERS.values())
                answers = await self.answer(
                    (
                        Question(
                            "Allow Cade to search the web for up-to-date information?",
                            (
                                (allow_label, ""),
                                ("Choose another provider", ""),
                                ("Disable web search", ""),
                            ),
                            "Web Search",
                        ),
                    )
                )
                if not answers or not answers[0]:
                    raise ValueError("Web search cancelled")
                choice = answers[0][0]
                selected: SearchSelection = "random"
                if choice == "Disable web search":
                    selected = False
                elif choice == "Choose another provider":
                    answers = await self.answer(
                        (
                            Question(
                                "Choose a provider for web search.",
                                tuple((value, "") for value in PROVIDERS.values()),
                                "Choose a web search provider",
                            ),
                        )
                    )
                    if not answers or not answers[0]:
                        raise ValueError("Web search cancelled")
                    selected = _selection(answers[0][0].lower())
                self.selection = selected
                if self.save_selection:
                    self.save_selection(selected)

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        limit: int = 256 * 1024,
    ) -> tuple[str, httpx.Headers]:
        async with client.stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            body = await collect_body(response, limit)
            return body.decode("utf-8", "replace"), response.headers

    async def query(self, provider: str, query: str) -> list[dict[str, Any]]:
        key = os.environ.get(provider.upper() + "_API_KEY")
        headers = {
            "User-Agent": "cade",
            "Accept": "application/json, text/event-stream",
        }
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with (
            asyncio.timeout(25),
            httpx.AsyncClient(
                transport=self.transport, follow_redirects=True, timeout=None
            ) as client,
        ):
            if provider == "tavily":
                headers.update(
                    {"X-Client-Name": "opencode2", "Accept": "application/json"}
                )
                if not key:
                    headers["X-Tavily-Access-Mode"] = "keyless"
                body, _ = await self._post(
                    client,
                    "https://api.tavily.com/search",
                    {
                        "query": query,
                        "search_depth": "basic",
                        "chunks_per_source": 3,
                        "max_results": 8,
                    },
                    headers,
                    limit=5 * 1024 * 1024,
                )
                return [
                    {
                        "url": item["url"],
                        "title": item["title"],
                        "content": item.get("content", ""),
                    }
                    for item in json.loads(body)["results"]
                ]
            url = _ENDPOINTS[provider]
            tool, arguments = {
                "exa": ("web_search_exa", {"query": query, "numResults": 8}),
                "firecrawl": ("firecrawl_search", {"query": query, "limit": 8}),
                "parallel": (
                    "web_search",
                    {"objective": query, "search_queries": [query]},
                ),
                "tinyfish": ("search", {"query": query}),
            }[provider]
            if provider == "exa":
                headers.pop("Authorization", None)
                if key:
                    url = str(httpx.URL(url).copy_add_param("exaApiKey", key))
            if provider == "tinyfish":
                headers.pop("Authorization", None)
                if key:
                    headers["X-API-Key"] = key
                    _, response_headers = await self._post(
                        client,
                        url,
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "clientInfo": {"name": "opencode", "version": "1.0"},
                            },
                        },
                        headers,
                    )
                    session = response_headers.get("mcp-session-id")
                    if session is None:
                        raise ValueError(
                            "TinyFish MCP initialize did not return an Mcp-Session-Id header"
                        )
                    headers["Mcp-Session-Id"] = session
                    await self._post(
                        client,
                        url,
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers,
                    )
                else:
                    headers["X-TinyFish-Access-Mode"] = "keyless"
            body, _ = await self._post(
                client,
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                headers,
            )
            result = _parse_mcp(body)
            if result is None:
                return []
            if provider == "parallel":
                return [
                    {
                        "url": item["url"],
                        "title": item.get("title"),
                        "content": "\n\n".join(item["excerpts"]),
                        "published": _published(item.get("publish_date")),
                    }
                    for item in result["structuredContent"]["results"]
                ]
            text = next(
                (
                    item["text"]
                    for item in result.get("content", [])
                    if item.get("text")
                ),
                "",
            )
            if provider == "exa":
                results: list[dict[str, Any]] = []
                for block in text.split("\n\n---\n\n"):
                    url_match = re.search(r"^URL:\s*(.+)$", block, re.MULTILINE)
                    if not url_match:
                        continue
                    title = re.search(r"^Title:\s*(.+)$", block, re.MULTILINE)
                    published = re.search(r"^Published:\s*(.+)$", block, re.MULTILINE)
                    content = re.search(
                        r"^(?:Highlights|Text):\s*\n?([\s\S]*)", block, re.MULTILINE
                    )
                    results.append(
                        {
                            "url": url_match[1].strip(),
                            "title": title[1].strip()
                            if title and title[1].strip() != "N/A"
                            else None,
                            "published": _published(
                                published[1].strip() if published else None
                            ),
                            "content": content[1].strip() if content else "",
                        }
                    )
                return results
            if not text:
                return []
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return []
            items = (
                decoded.get("data", {}).get("web", [])
                if provider == "firecrawl"
                else decoded.get("results", [])
            )
            return [
                {
                    "url": item["url"],
                    "title": item.get("title"),
                    "content": item.get(
                        "description" if provider == "firecrawl" else "snippet", ""
                    ),
                }
                for item in items
            ]

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None = None
    ) -> ToolResult:
        query = str(call.args["query"])
        provider: str | None = None
        session_id = current_tool_context().session_id
        try:
            await self.choose()
            if self.selection is False:
                raise ValueError("Web search is disabled")
            candidates = list(PROVIDERS)
            if self.selection != "random":
                provider = str(self.selection)
            else:
                provider = (
                    self.preferred[session_id]
                    if self.preferred.get(session_id) in candidates
                    else random.choice(candidates)
                )
            while provider:
                if self.selection == "random":
                    self.preferred[session_id] = provider
                cooldown = self.cooldowns.get(provider)
                if (
                    self.selection == "random"
                    and cooldown
                    and cooldown[0] > time.time()
                ):
                    error = cooldown[1]
                else:
                    try:
                        results = await self.query(provider, query)
                        self.preferred[session_id] = provider
                        blocks = []
                        for item in results:
                            block = f"## [{item.get('title') or item['url']}]({item['url']})"
                            if item.get("published"):
                                block += f"\nPublished: {item['published']}"
                            if item.get("content"):
                                block += "\n\n" + item["content"]
                            blocks.append(block)
                        return bound_web_output(
                            "\n\n".join(blocks)
                            if blocks
                            else "No search results found. Please try a different query.",
                            {"provider": provider},
                        )
                    except httpx.HTTPStatusError as exc:
                        if (
                            self.selection != "random"
                            or exc.response.status_code != 429
                        ):
                            raise
                        error = exc
                        retry = exc.response.headers.get("retry-after", "")
                        try:
                            delay = max(0.0, float(retry)) if retry.strip() else 60.0
                        except ValueError:
                            parsed = _published(retry)
                            delay = (
                                max(
                                    0.0,
                                    datetime.fromisoformat(parsed).timestamp()
                                    - time.time(),
                                )
                                if parsed
                                else 60.0
                            )
                        self.cooldowns[provider] = (time.time() + delay, exc)
                candidates.remove(provider)
                available = [
                    item
                    for item in candidates
                    if self.cooldowns.get(item, (0.0, None))[0] <= time.time()
                ]
                if not available:
                    raise error
                provider = random.choice(available)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            status = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            message = (
                {
                    429: "Web search rate limited (HTTP 429)",
                    401: "Web search authentication failed (HTTP 401)",
                }.get(status)
                if status
                else None
            )
            message = message or (
                f"Web search request failed (HTTP {status})"
                if status
                else f"Unable to search the web for {query}"
            )
            return ToolResult(
                message,
                is_error=True,
                metadata={"provider": provider, "cause": str(exc)},
            )
        return ToolResult(f"Unable to search the web for {query}", is_error=True)


def _selection(value: str) -> SearchSelection:
    if value not in PROVIDERS:
        raise ValueError("Web search provider not found")
    # UI 候选值来自固定提供方列表。
    return cast(SearchSelection, value)
