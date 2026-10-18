"""Cade Mini 压缩与换窗边界的回归测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    TextDelta,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.mini import (
    Budget,
    CompactionConfig,
    Compactor,
    SessionLog,
    SummaryMessage,
    project,
    run,
)


class FakeProvider:
    model = "fake"
    transport = "fake"

    def __init__(self, batches: list[list[ProviderEvent]]) -> None:
        self.batches = list(batches)
        self.requests: list[
            tuple[list[dict[str, Any]], list[ToolDefinition], StreamOptions | None]
        ] = []

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append((messages, tools, options))
        batch = self.batches.pop(0)

        async def events() -> AsyncIterator[ProviderEvent]:
            for event in batch:
                yield event

        return events()


def _log(tmp_path: Path) -> SessionLog:
    return SessionLog.create(tmp_path / "sessions", session_id="s1")


def _assistant(text: str) -> dict[str, Any]:
    return {
        "blocks": [{"type": "text", "text": text}],
        "model": "m",
        "provider": "p",
        "usage": None,
        "provider_state": None,
    }


def _summary_response(text: str = "## Goal\ncontinue the task") -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage("", "end_turn")]


async def test_compactor_summarizes_old_turns(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "old task", "source": "user"})
    log.append("assistant", _assistant("old answer"))
    log.append("user", {"text": "new task", "source": "user"})
    log.append("assistant", _assistant("new answer"))
    provider = FakeProvider([_summary_response()])
    compactor = Compactor(
        provider,
        config=CompactionConfig(keep_recent_tokens=50),
    )

    compacted = await compactor(log, Budget(100_000))

    assert compacted is True
    entries = log.branch()
    compaction = next(entry for entry in entries if entry.type == "compaction")
    assert compaction.data["first_kept_id"] == entries[-3].id
    assert str(compaction.data["summary"]).startswith("## Goal")
    roles = [message.role for message in project(entries)]
    assert roles == ["system", "summary", "user", "assistant"]
    summary = project(entries)[1]
    assert isinstance(summary, SummaryMessage)
    assert summary.kind == "compaction"
    summary_request = provider.requests[0][0]
    assert "old task" in str(summary_request)
    assert "new task" not in str(summary_request)


async def test_compactor_failure_appends_attempt(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old", "source": "user"})
    log.append("assistant", _assistant("old answer"))
    log.append("user", {"text": "new", "source": "user"})
    provider = FakeProvider([[ProviderFailure("boom", "Error")]])
    compactor = Compactor(provider)

    compacted = await compactor(log, Budget(100_000))

    assert compacted is False
    assert not any(entry.type == "compaction" for entry in log.branch())
    assert [entry.type for entry in log.branch()].count("attempt") == 1


async def test_compactor_needs_two_turns(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "only", "source": "user"})
    provider = FakeProvider([])
    compactor = Compactor(provider)

    assert await compactor(log, Budget(100_000)) is False
    assert provider.requests == []


async def test_loop_uses_compactor_before_request(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old " * 5_000, "source": "user"})
    log.append("assistant", _assistant("old answer " * 5_000))
    provider = FakeProvider([_summary_response("summary"), _summary_response("done")])
    compactor = Compactor(
        provider,
        config=CompactionConfig(keep_recent_tokens=10),
    )

    result = await run(
        log,
        "new task",
        provider=provider,
        tools=(),
        budget=Budget(context_window=2_000, output_reserve=0, headroom_tokens=0),
        compact=compactor,
    )

    assert result.termination == "completed"
    assert result.answer == "done"
    entries = log.branch()
    assert any(entry.type == "compaction" for entry in entries)
    main_request = provider.requests[1][0]
    content = "\n".join(str(message.get("content", "")) for message in main_request)
    assert "summary" in content
    assert "new task" in content
    assert "old " * 10 not in content


async def test_iterative_compaction_includes_prior_summary(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    log.append("system", {"text": "sys"})
    log.append("user", {"text": "task one", "source": "user"})
    log.append("assistant", _assistant("answer one"))
    user_two = log.append("user", {"text": "task two", "source": "user"})
    log.append("assistant", _assistant("answer two"))
    log.append(
        "compaction",
        {
            "summary": "prior summary",
            "first_kept_id": user_two.id,
            "tokens_before": 100,
            "model": "m",
            "provider": "p",
            "usage": None,
            "details": None,
        },
    )
    user_three = log.append("user", {"text": "task three", "source": "user"})
    provider = FakeProvider([_summary_response("new summary")])
    compactor = Compactor(
        provider,
        config=CompactionConfig(keep_recent_tokens=10),
    )

    compacted = await compactor(log, Budget(100_000))

    assert compacted is True
    entries = log.branch()
    new_compaction = entries[-1]
    assert new_compaction.type == "compaction"
    assert new_compaction.data["first_kept_id"] == user_three.id
    summary_request = provider.requests[0][0]
    text = str(summary_request)
    assert "PRIOR SUMMARY" in text
    assert "prior summary" in text
    assert "task two" in text
    assert [message.role for message in project(entries)] == [
        "system",
        "summary",
        "user",
    ]


async def test_compaction_config_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        CompactionConfig(keep_recent_tokens=0)
    with pytest.raises(ValueError):
        CompactionConfig(max_summary_chars=0)
