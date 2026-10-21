"""工具声明与执行结果契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from cade.harness.cancellation import CancellationToken
from cade.harness.session.types import (
    ToolCallBlock,
)


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
