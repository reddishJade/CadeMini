"""Cade Mini 自动审批复核的回归测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from cade.ai.events import FinalMessage, ProviderEvent, TextDelta
from cade.ai.types import StreamOptions, ToolDefinition
from cade.mini import AutoReviewer, ToolCallBlock


class FakeProvider:
    model = "fake"
    transport = "fake"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, tools, options

        async def events() -> AsyncIterator[ProviderEvent]:
            yield TextDelta(self.answer)
            yield FinalMessage(self.answer, "end_turn")

        return events()


def _call() -> ToolCallBlock:
    return ToolCallBlock(
        id="w1",
        name="write",
        args={"path": "a.txt", "content": "x"},
    )


def test_reviewer_allows_and_denies(tmp_path: Path) -> None:
    allow = AutoReviewer(FakeProvider("ALLOW"), workspace=tmp_path)
    deny = AutoReviewer(FakeProvider("DENY"), workspace=tmp_path)

    assert asyncio.run(allow(_call())) is True
    assert asyncio.run(deny(_call())) is False
