"""Cade Mini agent 循环的回归测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

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
from cade.mini import (
    Budget,
    CancellationToken,
    SessionLog,
    TextDeltaEvent,
    ToolCallBlock,
    ToolEndEvent,
    ToolResult,
    ToolStartEvent,
    project,
    run,
)


class FakeProvider:
    model = "fake"
    transport = "fake"

    def __init__(self, batches: list[list[ProviderEvent]]) -> None:
        self.batches = list(batches)
        self.requests: list[
            tuple[
                list[dict[str, Any]],
                list[ToolDefinition],
                StreamOptions | None,
            ]
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


class EchoTool:
    name: str = "echo"
    description: str = "Echo the text argument."

    def __init__(self) -> None:
        self.calls: list[ToolCallBlock] = []
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        self.calls.append(call)
        return ToolResult(content=str(call.args.get("text", "")))


class AllowAll:
    def check(self, call: ToolCallBlock) -> Literal["allow"]:
        return "allow"


class DenyAll:
    def check(self, call: ToolCallBlock) -> Literal["deny"]:
        return "deny"


def _log(tmp_path: Path) -> SessionLog:
    return SessionLog.create(tmp_path / "sessions", session_id="s1")


def _text_response(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


def _tool_response(call_id: str, text: str = "hi") -> list[ProviderEvent]:
    return [
        ToolCallEvent([ToolCall(id=call_id, name="echo", input={"text": text})]),
        FinalMessage("", "tool_use"),
    ]


async def test_run_completes_without_tools(tmp_path: Path) -> None:
    provider = FakeProvider([_text_response("hello")])
    log = _log(tmp_path)

    result = await run(
        log,
        "hi",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
    )

    assert result.termination == "completed"
    assert result.answer == "hello"
    assert result.steps == 1
    assert [entry.type for entry in log.branch()] == [
        "meta",
        "user",
        "request",
        "assistant",
        "run",
    ]
    assert [message.role for message in project(log.branch())] == [
        "user",
        "assistant",
    ]


async def test_run_executes_tool_and_continues(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _tool_response("c1"),
            _text_response("done"),
        ]
    )
    log = _log(tmp_path)
    tool = EchoTool()

    result = await run(
        log,
        "run echo",
        provider=provider,
        tools=(tool,),
        budget=Budget(100_000),
    )

    assert result.termination == "completed"
    assert result.answer == "done"
    assert len(provider.requests) == 2
    assert len(tool.calls) == 1
    branch = log.branch()
    assert [entry.type for entry in branch].count("assistant") == 2
    tool_result = next(entry for entry in branch if entry.type == "tool_result")
    assert tool_result.data["content"] == "hi"
    assert tool_result.data["status"] == "ok"


async def test_run_denied_tool_returns_error_result(tmp_path: Path) -> None:
    provider = FakeProvider([_tool_response("c1"), _text_response("done")])
    log = _log(tmp_path)
    tool = EchoTool()

    result = await run(
        log,
        "run echo",
        provider=provider,
        tools=(tool,),
        budget=Budget(100_000),
        policy=DenyAll(),
    )

    assert result.termination == "completed"
    assert tool.calls == []
    tool_result = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert tool_result.data["status"] == "error"
    assert "Permission denied" in str(tool_result.data["content"])


async def test_run_unknown_tool_returns_error_result(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                ToolCallEvent([ToolCall(id="c1", name="missing", input={})]),
                FinalMessage("", "tool_use"),
            ],
            _text_response("done"),
        ]
    )
    log = _log(tmp_path)

    result = await run(
        log,
        "call missing",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
    )

    assert result.termination == "completed"
    tool_result = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert tool_result.data["status"] == "error"
    assert "Unknown tool" in str(tool_result.data["content"])


async def test_run_step_limit(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            _tool_response("c1"),
            _tool_response("c2"),
        ]
    )
    log = _log(tmp_path)

    result = await run(
        log,
        "loop",
        provider=provider,
        tools=(EchoTool(),),
        budget=Budget(100_000),
        max_steps=2,
    )

    assert result.termination == "step_limit"
    assert result.steps == 2
    assert len(provider.requests) == 2
    run_entry = log.branch()[-1]
    assert run_entry.type == "run"
    assert run_entry.data["termination"] == "step_limit"


async def test_run_provider_failure_retries_once(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [ProviderFailure("timeout", "TimeoutError")],
            _text_response("recovered"),
        ]
    )
    log = _log(tmp_path)

    result = await run(
        log,
        "hi",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        max_provider_retries=1,
        retry_delay_seconds=0,
    )

    assert result.termination == "completed"
    assert result.answer == "recovered"
    assert [entry.type for entry in log.branch()].count("attempt") == 1


async def test_run_context_over_budget_without_compaction(tmp_path: Path) -> None:
    provider = FakeProvider([])
    log = _log(tmp_path)

    result = await run(
        log,
        "x" * 100_000,
        provider=provider,
        tools=(),
        budget=Budget(context_window=1_000, output_reserve=0, headroom_tokens=0),
    )

    assert result.termination == "context_error"
    assert provider.requests == []
    assert [entry.type for entry in log.branch()] == ["meta", "user", "run"]


async def test_run_cancelled_before_first_step(tmp_path: Path) -> None:
    provider = FakeProvider([])
    log = _log(tmp_path)
    token = CancellationToken()
    token.cancel("stop")

    result = await run(
        log,
        "hi",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        cancel=token,
    )

    assert result.termination == "aborted"
    assert result.error == "stop"
    assert provider.requests == []


async def test_run_uses_compaction_hook_before_giving_up(
    tmp_path: Path,
) -> None:
    called: list[bool] = []

    async def compact(log: SessionLog, budget: Budget) -> bool:
        called.append(True)
        return True

    log = _log(tmp_path)

    result = await run(
        log,
        "hi",
        provider=FakeProvider([]),
        tools=(),
        budget=Budget(context_window=1_000, output_reserve=0, headroom_tokens=0),
        context=("x" * 100_000,),
        compact=compact,
    )

    assert called == [True]
    assert result.termination == "context_error"


async def test_run_compacts_before_admission_drops_old_turns(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old " * 5_000, "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "old answer " * 5_000}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )
    provider = FakeProvider([_text_response("done")])
    called: list[bool] = []

    async def compact(log: SessionLog, budget: Budget) -> bool:
        called.append(True)
        entries = log.branch()
        latest_user = next(entry for entry in reversed(entries) if entry.type == "user")
        log.append(
            "window",
            {
                "start_id": latest_user.id,
                "reason": "manual",
                "handoff": None,
            },
        )
        return True

    result = await run(
        log,
        "new",
        provider=provider,
        tools=(),
        budget=Budget(context_window=2_000, output_reserve=0, headroom_tokens=0),
        compact=compact,
    )

    assert called == [True]
    assert result.termination == "completed"
    assert result.answer == "done"
    first_request = provider.requests[0][0]
    assert all(
        "old" not in str(message.get("content", "")) for message in first_request
    )


async def test_run_reports_usage_and_max_tokens(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                UsageUpdate(10, 5),
                TextDelta("partial"),
                FinalMessage("partial", "max_tokens"),
            ]
        ]
    )
    log = _log(tmp_path)

    result = await run(
        log,
        "hi",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
    )

    assert result.termination == "max_tokens"
    assert result.answer == "partial"
    assert result.usage == {"input": 10, "output": 5}
    run_entry = log.branch()[-1]
    assert run_entry.data["usage"] == {"input": 10, "output": 5}


async def test_run_empty_response_is_a_provider_error(tmp_path: Path) -> None:
    provider = FakeProvider([[FinalMessage("", "end_turn")]])
    log = _log(tmp_path)

    result = await run(
        log,
        "hi",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
    )

    assert result.termination == "provider_error"
    assert result.error == "empty model response"
    assert [entry.type for entry in log.branch()].count("attempt") == 1


async def test_run_emits_stream_and_tool_events(tmp_path: Path) -> None:
    provider = FakeProvider([_tool_response("c1"), _text_response("done")])
    log = _log(tmp_path)
    events: list[object] = []

    await run(
        log,
        "run echo",
        provider=provider,
        tools=(EchoTool(),),
        budget=Budget(100_000),
        on_event=events.append,
    )

    assert any(isinstance(event, ToolStartEvent) for event in events)
    assert any(isinstance(event, ToolEndEvent) for event in events)
    assert any(isinstance(event, TextDeltaEvent) for event in events)
