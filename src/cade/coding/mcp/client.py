"""沿用 Cade 的单任务会话所有权，适配官方 SDK 的两代协议。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import tempfile
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from mcp import Client, StdioServerParameters, types
from mcp.client.session import ClientRequestContext, IncomingMessage
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp_types.version import KNOWN_PROTOCOL_VERSIONS
from pydantic import BaseModel
from pydantic.networks import FileUrl

from cade.coding.mcp_config import McpServerConfig
from cade.harness.async_worker import IsolatedAsyncWorker

SUPPORTED_PROTOCOL_VERSIONS = KNOWN_PROTOCOL_VERSIONS
MAX_TOOL_LIST_PAGES = 100


class McpError(RuntimeError):
    """MCP 连接或请求失败。"""


@dataclass(frozen=True)
class _Command:
    operation: Callable[[Client], Coroutine[Any, Any, Any]]
    future: concurrent.futures.Future[Any]


def model_dict(model: BaseModel) -> dict[str, Any]:
    """保留协议字段别名。"""
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


def _error_text(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return "; ".join(_error_text(child) for child in error.exceptions)
    return str(error)


class McpClient:
    """在独立 owner task 中连接、执行请求并关闭 SDK 上下文。"""

    def __init__(self, config: McpServerConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace.resolve()
        self.protocol_version: str | None = None
        self.capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self.changed = threading.Event()
        self.connected = False
        self._worker = IsolatedAsyncWorker(name="cade-mcp")
        self._queue: asyncio.Queue[_Command | None] | None = None
        self._pending: set[concurrent.futures.Future[Any]] = set()
        self._lock = threading.Lock()
        self._secrets: tuple[str, ...] = ()
        self._stderr_dir = self.workspace / ".cade" / "mcp"
        self._stderr_dir.mkdir(parents=True, exist_ok=True)
        self._stderr = tempfile.TemporaryFile(  # noqa: SIM115
            mode="w+", encoding="utf-8", errors="replace", dir=self._stderr_dir
        )
        self._owner: concurrent.futures.Future[None] | None = None
        self._closed = False
        self._stopped = threading.Event()

    def redact(self, text: str) -> str:
        """从诊断和工具结果中移除本连接显式传入的凭证。"""
        forms = {
            form
            for secret in self._secrets
            if secret
            for form in (
                secret,
                json.dumps(secret, ensure_ascii=False)[1:-1],
                json.dumps(secret, ensure_ascii=True)[1:-1],
            )
        }
        for form in sorted(forms, key=len, reverse=True):
            text = text.replace(form, "[redacted]")
        return text

    def redact_data(self, value: Any) -> Any:
        """递归脱敏 JSON 字符串，保留数字、布尔值和容器结构。"""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_data(item) for item in value]
        if isinstance(value, dict):
            return {
                self.redact(str(key)): self.redact_data(item)
                for key, item in value.items()
            }
        return value

    def _environment(self, names: dict[str, str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for target, source in names.items():
            value = os.environ.get(source)
            if value is None:
                raise McpError(f"Missing MCP environment variable: {source}")
            values[target] = value
        self._secrets = (*self._secrets, *values.values())
        return values

    def start(self) -> None:
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._owner = self._worker.submit(self._serve(ready))
        try:
            ready.result(timeout=self.config.timeout_ms / 1000)
        except Exception as exc:  # noqa: BLE001
            # SDK 和进程创建异常在连接边界统一转换。
            diagnostic = self._diagnostic()
            self.close()
            raise McpError(
                self.redact(f"MCP initialization failed: {exc}. {diagnostic}")
            ) from None

    def _diagnostic(self) -> str:
        if self._stderr.closed:
            return ""
        self._stderr.flush()
        self._stderr.seek(0)
        return self.redact(self._stderr.read()[-2000:])

    async def _serve(self, ready: concurrent.futures.Future[None]) -> None:
        from contextlib import AsyncExitStack

        try:
            async with AsyncExitStack() as stack:
                if self.config.transport == "stdio":
                    env = dict(self.config.env or {})
                    self._secrets = tuple(env.values())
                    env.update(self._environment(self.config.env_from))
                    params = StdioServerParameters(
                        command=self.config.command or "",
                        args=self.config.args,
                        env=env,
                        cwd=self.workspace / (self.config.cwd or "."),
                    )
                    transport = stdio_client(params, errlog=cast(TextIO, self._stderr))
                else:
                    headers = self._environment(self.config.headers_from)
                    http = await stack.enter_async_context(
                        create_mcp_http_client(headers=headers)
                    )
                    transport = streamable_http_client(
                        self.config.url or "", http_client=http
                    )
                async with Client(
                    transport,
                    mode=self.config.protocol,
                    read_timeout_seconds=self.config.timeout_ms / 1000,
                    list_roots_callback=self._roots,
                    message_handler=self._notification,
                    client_info=types.Implementation(name="cade", version="0.1.2"),
                    cache=None,
                ) as client:
                    self.protocol_version = client.protocol_version
                    self.capabilities = model_dict(client.server_capabilities)
                    self.server_info = (
                        model_dict(client.server_info) if client.server_info else {}
                    )
                    self.connected = True
                    self._queue = asyncio.Queue()
                    ready.set_result(None)
                    while (command := await self._queue.get()) is not None:
                        if command.future.cancelled():
                            continue
                        try:
                            result = await command.operation(client)
                            self.capabilities = model_dict(client.server_capabilities)
                        except Exception as exc:  # noqa: BLE001
                            # 第三方 SDK 异常只影响当前工具调用。
                            if not command.future.done():
                                command.future.set_exception(exc)
                        else:
                            if not command.future.done():
                                command.future.set_result(result)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
            if not ready.done():
                ready.set_exception(McpError(_error_text(exc)))
        finally:
            self.connected = False
            with self._lock:
                for future in self._pending:
                    if not future.done():
                        future.set_exception(McpError("MCP connection closed"))
            self._stopped.set()

    async def _roots(self, context: ClientRequestContext) -> types.ListRootsResult:
        del context
        return types.ListRootsResult(
            roots=[
                types.Root(
                    uri=FileUrl(self.workspace.as_uri()), name=self.workspace.name
                )
            ]
        )

    async def _notification(self, message: IncomingMessage) -> None:
        if isinstance(message, types.ServerNotification) and message.method in {
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        }:
            self.changed.set()

    async def request(
        self,
        operation: Callable[[Client], Coroutine[Any, Any, Any]],
    ) -> Any:
        if not self.connected:
            raise McpError("MCP connection is closed")
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            self._pending.add(future)

        async def enqueue() -> None:
            if self._queue is None:
                raise McpError("MCP connection is closed")
            await self._queue.put(_Command(operation, future))

        try:
            await asyncio.wrap_future(self._worker.submit(enqueue()))
            return await asyncio.wait_for(
                asyncio.wrap_future(future), self.config.timeout_ms / 1000
            )
        except (asyncio.CancelledError, TimeoutError):
            # 取消后等子进程回收，再允许推理循环继续。
            await asyncio.to_thread(self.close)
            raise
        except Exception as exc:  # noqa: BLE001
            diagnostic = self._diagnostic()
            await asyncio.to_thread(self.close)
            raise McpError(self.redact(f"{_error_text(exc)}. {diagnostic}")) from None
        finally:
            with self._lock:
                self._pending.discard(future)

    async def list_tools(self) -> list[dict[str, Any]]:
        if not isinstance(self.capabilities.get("tools"), dict):
            return []

        async def list_all(client: Client) -> list[dict[str, Any]]:
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            seen: set[str] = set()
            for _ in range(MAX_TOOL_LIST_PAGES):
                page = await client.list_tools(cursor=cursor)
                tools.extend(model_dict(tool) for tool in page.tools)
                cursor = page.next_cursor
                if cursor is None:
                    return tools
                if not cursor or cursor in seen:
                    raise McpError("MCP tools/list returned an invalid cursor")
                seen.add(cursor)
            raise McpError("MCP tools/list exceeded the page limit")

        return await self.request(list_all)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.connected = False
        if self._owner is not None:
            self._owner.cancel()
            # 先让 SDK 完成取消和进程回收，再停止其事件循环。
            if not self._stopped.wait(timeout=10):
                self._worker.close()
                raise McpError("MCP transport did not stop within 10 seconds")
        self._worker.close()
        self._stderr.close()
