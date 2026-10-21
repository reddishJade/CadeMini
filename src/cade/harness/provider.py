"""循环、压缩器和复核器使用的模型流契约。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from cade.ai.events import (
    ProviderEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition


class Provider(Protocol):
    """循环所需的 provider 面；由 cade.ai adapter 结构满足。"""

    @property
    def model(self) -> str: ...

    @property
    def transport(self) -> str: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]: ...
