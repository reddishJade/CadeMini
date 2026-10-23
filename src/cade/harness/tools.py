"""工具声明与执行结果契约。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import (
    ToolCallBlock,
)


@dataclass(frozen=True)
class ToolContext:
    """当前会话元数据和工具进度回调，不写入工具参数。"""

    session_id: str = ""
    session_file: str = ""
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    on_update: Callable[[str], None] | None = None


_CONTEXT: ContextVar[ToolContext | None] = ContextVar("tool_context", default=None)


def current_tool_context() -> ToolContext:
    return _CONTEXT.get() or ToolContext()


@contextmanager
def tool_context(context: ToolContext) -> Iterator[None]:
    token = _CONTEXT.set(context)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@dataclass(frozen=True)
class ToolResult:
    """工具返回值；正文会进入 tool_result 事实。"""

    content: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


class Tool(Protocol):
    """循环可执行的工具。"""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None,
    ) -> ToolResult: ...
