"""Cade Mini 第一段的验收契约。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from cade_mini.ai.events import FinalMessage, ProviderEvent
from cade_mini.ai.types import StreamOptions, ToolDefinition
from cade_mini.core.log import SessionLog
from cade_mini.core.loop import run
from cade_mini.core.projection import project
from cade_mini.core.request import Budget


class OneShotProvider:
    model = "fake"
    transport = "fake"

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del messages, tools, options

        async def events() -> AsyncIterator[ProviderEvent]:
            yield FinalMessage("hello", "end_turn")

        return events()


def test_reopened_log_projects_identically(tmp_path: Path) -> None:
    log = SessionLog.create(tmp_path / "sessions", session_id="s1")
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "hi", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "hello"}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )

    reopened = SessionLog.open(log.path)

    assert project(log.branch()) == project(reopened.branch())
    assert [message.role for message in project(reopened.branch())] == [
        "system",
        "user",
        "assistant",
    ]


async def test_log_contains_only_fact_types(tmp_path: Path) -> None:
    log = SessionLog.create(tmp_path / "sessions", session_id="s2")

    result = await run(
        log,
        "hi",
        provider=OneShotProvider(),
        tools=(),
        budget=Budget(100_000),
    )

    assert result.answer == "hello"
    entries = log.branch()
    types = [entry.type for entry in entries]
    assert "final" not in types
    assert "tool_use" not in types
    assert all("replacement" not in entry.data for entry in entries)
    assert types.count("assistant") == 1
    assert types[-1] == "run"
