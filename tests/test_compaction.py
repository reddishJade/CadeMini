"""Cade 压缩与换窗边界的回归测试。"""

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
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.coding.tools.history import HistoryTool
from cade.harness.cancellation import CancellationToken
from cade.harness.compaction import CompactionConfig, Compactor
from cade.harness.loop import run
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import (
    AssistantMessage,
    SummaryMessage,
    ToolCallBlock,
    ToolResultMessage,
)
from cade.harness.tools import ToolResult
from cade.harness.window import reset_window


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


async def test_compactor_leaves_a_lone_user_message_intact(tmp_path: Path) -> None:
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


async def test_failed_compaction_uses_logged_window_before_main_request(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old " * 5_000})
    log.append("assistant", _assistant("old answer " * 5_000))
    provider = FakeProvider(
        [
            [ProviderFailure("summary failed", "Error")],
            _summary_response("done"),
        ]
    )
    result = await run(
        log,
        "new task",
        provider=provider,
        tools=(),
        budget=Budget(2_000, output_reserve=0, headroom_tokens=0),
        compact=Compactor(provider),
        window=lambda log: reset_window(log, reason="fallback"),
    )
    assert result.termination == "completed"
    entries = log.branch()
    window_index = next(i for i, entry in enumerate(entries) if entry.type == "window")
    request_index = next(
        i for i, entry in enumerate(entries) if entry.type == "request"
    )
    assert window_index < request_index
    assert not any(entry.type == "compaction" for entry in entries)
    assert provider.requests[-1][0] == [{"role": "user", "content": "new task"}]
    assert any(entry.data.get("text") == "old " * 5_000 for entry in entries)


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


async def test_compactor_includes_working_note(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old task", "source": "user"})
    log.append("assistant", _assistant("old answer"))
    log.append("user", {"text": "new task", "source": "user"})
    provider = FakeProvider([_summary_response("summary")])
    compactor = Compactor(provider, extra_context=lambda: "NOTE STATE")

    compacted = await compactor(log, Budget(100_000))

    assert compacted is True
    assert "NOTE STATE" in str(provider.requests[0][0])


async def test_compaction_config_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        CompactionConfig(keep_recent_tokens=0)
    with pytest.raises(ValueError):
        CompactionConfig(max_summary_chars=0)


@pytest.mark.parametrize(
    "failed,fallback", [(False, False), (True, True), (True, False)]
)
async def test_run_counts_compaction_usage(
    tmp_path: Path, failed: bool, fallback: bool
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old " * 5_000})
    old_answer = _assistant("old answer " * 5_000)
    old_answer["usage"] = {"input": 999, "output": 999}
    log.append("assistant", old_answer)
    summary: list[ProviderEvent] = [UsageUpdate(100, 20)]
    summary += (
        [ProviderFailure("summary failed", "Error")]
        if failed
        else _summary_response("summary")
    )
    provider = FakeProvider([summary, [UsageUpdate(30, 4), *_summary_response("done")]])
    result = await run(
        log,
        "new task",
        provider=provider,
        tools=(),
        budget=Budget(2_000, output_reserve=0, headroom_tokens=0),
        compact=Compactor(provider),
        window=(lambda log: reset_window(log, reason="fallback")) if fallback else None,
    )
    admitted = not failed or fallback
    expected = (
        {"input": 130, "output": 24} if admitted else {"input": 100, "output": 20}
    )
    assert result.termination == ("completed" if admitted else "context_error")
    assert result.usage == expected
    assert log.branch()[-1].data["usage"] == expected

    resumed = await run(
        SessionLog.open(log.path),
        "follow up",
        provider=FakeProvider([[UsageUpdate(7, 2), *_summary_response("next")]]),
        tools=(),
        budget=Budget(100_000),
    )
    assert resumed.usage == {"input": 7, "output": 2}


@pytest.mark.parametrize("reopen", [False, True])
async def test_compaction_advances_within_one_user_task(
    tmp_path: Path, reopen: bool
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "finish the backend and verify it"})
    provider = FakeProvider(
        [_summary_response("first summary"), _summary_response("second summary")]
    )
    compactor = Compactor(provider, config=CompactionConfig(keep_recent_tokens=20))
    results: dict[str, str] = {}
    cuts: list[str] = []
    for stage in range(2):
        for index in range(3):
            call_id = f"{stage}-{index}"
            assistant = _assistant("")
            assistant["blocks"] = [
                {"type": "tool_call", "id": call_id, "name": "read", "args": {}}
            ]
            log.append("assistant", assistant)
            # 空 assistant 和审计事实不应干扰消息与事件的定位。
            log.append("assistant", {"blocks": []})
            log.append("attempt", {"model": "m", "provider": "p", "error": "audit"})
            content = f"evidence {call_id} " * 10
            results[call_id] = content
            log.append(
                "tool_result", {"call_id": call_id, "status": "ok", "content": content}
            )
        before = log.entries()
        assert await compactor(log, Budget(500, output_reserve=0, headroom_tokens=0))
        assert log.entries()[:-1] == before
        cuts.append(str(log.branch()[-1].data["first_kept_id"]))
        if reopen:
            log = SessionLog.open(log.path)
        visible = project(log.branch())
        assert [message.role for message in visible] == [
            "summary",
            "assistant",
            "tool_result",
        ]
        assert isinstance(visible[-1], ToolResultMessage)
        assert visible[-1].call_id == f"{stage}-2"
        assert visible[-1].content == results[f"{stage}-2"]
    assert cuts[0] != cuts[1]
    assert "PRIOR SUMMARY:\nfirst summary" in provider.requests[1][0][1]["content"]
    assert len([entry for entry in log.branch() if entry.type == "user"]) == 1
    assert {
        str(entry.data["call_id"]): entry.data["content"]
        for entry in log.branch()
        if entry.type == "tool_result"
    } == results


async def test_compaction_keeps_a_parallel_tool_batch_together(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "original objective"})
    log.append("assistant", _assistant("old progress " * 100))
    call = log.append(
        "assistant",
        {
            "blocks": [
                {"type": "tool_call", "id": call_id, "name": "read", "args": {}}
                for call_id in ("a", "b")
            ],
            "provider_state": {"replay": "retained"},
        },
    )
    for call_id in ("a", "b"):
        log.append(
            "tool_result",
            {"call_id": call_id, "status": "ok", "content": "evidence " * 10},
        )
    provider = FakeProvider([_summary_response()])
    assert await Compactor(provider, config=CompactionConfig(keep_recent_tokens=1))(
        log, Budget(1000, output_reserve=0, headroom_tokens=0)
    )
    assert log.branch()[-1].data["first_kept_id"] == call.id
    visible = project(log.branch())
    assert [message.role for message in visible] == [
        "summary",
        "assistant",
        "tool_result",
        "tool_result",
    ]
    assert isinstance(visible[1], AssistantMessage)
    assert visible[1].provider_state == {"replay": "retained"}


async def test_compaction_cannot_hide_an_oversized_latest_tool_batch(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "task"})
    log.append("assistant", _assistant("earlier progress"))
    log.append(
        "assistant",
        {"blocks": [{"type": "tool_call", "id": "c1", "name": "read", "args": {}}]},
    )
    log.append("tool_result", {"call_id": "c1", "status": "ok", "content": "x" * 10000})
    provider = FakeProvider([])
    assert not await Compactor(provider)(
        log, Budget(500, output_reserve=0, headroom_tokens=0)
    )
    assert provider.requests == []


async def test_single_task_runs_across_repeated_compactions(tmp_path: Path) -> None:
    class EvidenceTool:
        name = "evidence"
        description = "Return verification evidence."

        def __init__(self) -> None:
            self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None = None
        ) -> ToolResult:
            return ToolResult(content=f"verified {call.id} " * 50)

    log = _log(tmp_path)
    provider = FakeProvider(
        [
            [
                ToolCallEvent([ToolCall(id=f"c{i}", name="evidence", input={})]),
                FinalMessage("", "tool_use"),
            ]
            for i in range(12)
        ]
        + [_summary_response("backend verified")]
    )
    summarizer = FakeProvider(
        [
            _summary_response(
                "Goal: finish and verify the backend; continue remaining checks."
            )
            for _ in range(12)
        ]
    )
    result = await run(
        log,
        "finish and verify the backend",
        provider=provider,
        tools=(EvidenceTool(),),
        budget=Budget(700, output_reserve=0, headroom_tokens=0),
        compact=Compactor(summarizer),
    )
    assert result.termination == "completed"
    assert result.answer == "backend verified"
    entries = log.branch()
    assert len([entry for entry in entries if entry.type == "user"]) == 1
    assert len([entry for entry in entries if entry.type == "compaction"]) >= 2
    assert len([entry for entry in entries if entry.type == "tool_result"]) == 12
    assert len(provider.requests) == 13
    for messages, _, _ in provider.requests:
        calls = {
            call["id"] for message in messages for call in message.get("tool_calls", [])
        }
        results = {
            message["tool_call_id"] for message in messages if message["role"] == "tool"
        }
        assert calls == results
    reopened = SessionLog.open(log.path)
    assert project(reopened.branch()) == project(entries)
    evidence = HistoryTool(tmp_path, reopened).run({"call_id": "c0"})
    assert "verified c0 " * 50 in evidence.content
