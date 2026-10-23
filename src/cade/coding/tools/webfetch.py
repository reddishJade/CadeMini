"""获取 HTTP 内容，检查类型并转换为所需格式。"""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from cade.coding.tools.base import number_arg
from cade.coding.tools.html_markdown import (
    MAX_MARKDOWN_BYTES,
    html_to_markdown,
    html_to_text,
)
from cade.coding.tools.truncate import truncate
from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult

USER_AGENT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OpenCode-User/1.0; +https://opencode.ai"
_ACCEPT = {
    "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
    "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
    "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1",
}


async def collect_body(response: httpx.Response, limit: int) -> bytes:
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise ValueError(f"Response too large (exceeds {limit} byte limit)")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError(f"Response too large (exceeds {limit} byte limit)")
    return bytes(body)


def bound_web_output(content: str, metadata: dict[str, Any]) -> ToolResult:
    truncation = truncate(content)
    if not truncation.truncated:
        return ToolResult(content, metadata={**metadata, "truncated": False})
    with tempfile.NamedTemporaryFile(
        prefix="cade-tool-", suffix=".txt", delete=False
    ) as stream:
        stream.write(content.encode("utf-8"))
        file = stream.name
    shown = f"lines 1-{truncation.outputLines}" if truncation.outputLines else "0 lines"
    marker = (
        f"[showing {shown} of {truncation.totalLines}; full output saved to {file}]"
    )
    preview = truncation.content + ("\n" if truncation.content else "") + marker
    return ToolResult(
        preview, metadata={**metadata, "truncated": True, "outputPath": file}
    )


class WebFetchTool:
    name = "webfetch"
    description = """Fetch content from an HTTP or HTTPS URL and return it as text, markdown, or HTML. Markdown is the default.

Use a more targeted tool when one is available. This tool is read-only. Large text results may be replaced with a preview while the complete output is retained in managed storage."""
    manages_timeout = True
    _parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The HTTP or HTTPS URL to fetch content from",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown", "html"],
                "default": "markdown",
                "description": "The format to return the content in. Defaults to markdown.",
            },
            "timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": 120,
                "description": "Optional timeout in seconds (maximum: 120)",
            },
        },
        "required": ["url"],
    }

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None = None
    ) -> ToolResult:
        url = str(call.args["url"])
        format_name = str(call.args.get("format") or "markdown")
        timeout = number_arg(call.args, "timeout", 30)
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("URL must use http:// or https://")
            async with (
                asyncio.timeout(timeout),
                httpx.AsyncClient(
                    transport=self.transport, follow_redirects=True, timeout=None
                ) as client,
            ):
                headers = {
                    "User-Agent": USER_AGENT,
                    "Accept": _ACCEPT[format_name],
                    "Accept-Language": "en-US,en;q=0.9",
                }
                body: bytes = b""
                content_type = ""
                for attempt in range(2):
                    async with client.stream("GET", url, headers=headers) as response:
                        if (
                            attempt == 0
                            and response.status_code == 403
                            and response.headers.get("cf-mitigated") == "challenge"
                        ):
                            headers["User-Agent"] = "opencode"
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "")
                        mime = content_type.split(";", 1)[0].strip().lower()
                        if mime.startswith("image/") and mime not in {
                            "image/svg+xml",
                            "image/vnd.fastbidsheet",
                        }:
                            raise ValueError(
                                f"Unsupported fetched image content type: {mime}"
                            )
                        if mime and not (
                            mime.startswith("text/")
                            or mime
                            in {
                                "application/json",
                                "application/xml",
                                "application/javascript",
                                "application/x-javascript",
                            }
                            or mime.endswith(("+json", "+xml"))
                        ):
                            raise ValueError(
                                f"Unsupported fetched file content type: {mime}"
                            )
                        body = await collect_body(response, MAX_MARKDOWN_BYTES)
                    break
            content = body.decode("utf-8-sig", "replace")
            if "text/html" in content_type:
                if format_name == "markdown":
                    content = html_to_markdown(content)
                elif format_name == "text":
                    content = html_to_text(content)
            return bound_web_output(content, {"contentType": content_type})
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            return ToolResult(
                f"Unable to fetch {url}", is_error=True, metadata={"cause": str(exc)}
            )
