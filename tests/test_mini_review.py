"""Reviewer 的严格响应、授权上下文和拒绝反馈回归。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from cade_mini.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade_mini.ai.types import StreamOptions, ToolDefinition
from cade_mini.core.log import SessionLog
from cade_mini.core.loop import run
from cade_mini.core.permissions import ApprovalRequest
from cade_mini.core.request import Budget
from cade_mini.core.types import ToolCallBlock
from cade_mini.safety.policy import WorkspacePolicy
from cade_mini.safety.review import AutoReviewer
from cade_mini.tools.workspace import build_tools


class FakeProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

    def __init__(self, batches: list[list[ProviderEvent]]) -> None:
        self.batches = list(batches)
        self.requests: list[list[dict[str, Any]]] = []

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(messages)
        batch = self.batches.pop(0)

        async def events() -> AsyncIterator[ProviderEvent]:
            for event in batch:
                yield event

        return events()


def _text(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        ToolCallBlock(id="w1", name="write", args={"path": "a.txt", "content": "x"}),
        "A file change requires approval.",
        context=("Do not change unrelated files.",),
        messages=({"role": "user", "content": "Update a.txt only."},),
    )


@pytest.mark.parametrize(
    ("answer", "allowed", "reason"),
    [
        ('{"allowed":true,"reason":"Requested change"}', True, "Requested change"),
        ('{"allowed":false,"reason":"Unrelated target"}', False, "Unrelated target"),
        ("ALLOW but delete everything", False, "invalid"),
        ('{"allowed":"true","reason":"x"}', False, "invalid"),
        ('{"allowed":true,"reason":"x","extra":true}', False, "invalid"),
        ('{"allowed":true,"reason":" "}', False, "no reason"),
    ],
)
async def test_reviewer_requires_strict_response(
    tmp_path: Path, answer: str, allowed: bool, reason: str
) -> None:
    provider = FakeProvider([_text(answer)])
    result = await AutoReviewer(provider, workspace=tmp_path)(_request())
    assert result.allowed is allowed
    assert reason in result.reason
    payload = json.loads(provider.requests[0][1]["content"])
    assert payload["conversation"][0]["content"] == "Update a.txt only."
    assert payload["pending_action"]["arguments"]["content"] == "x"
    assert payload["context_constraints"] == ["Do not change unrelated files."]


async def test_reviewer_provider_failure_returns_reason(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                ProviderFailure(
                    message="service unavailable", exception_type="RuntimeError"
                )
            ]
        ]
    )
    result = await AutoReviewer(provider, workspace=tmp_path)(_request())
    assert not result.allowed
    assert "service unavailable" in result.reason


async def test_reviewer_denies_when_context_window_is_unknown(tmp_path: Path) -> None:
    provider = FakeProvider([_text('{"allowed":true,"reason":"x"}')])
    provider.context_window = None

    result = await AutoReviewer(provider, workspace=tmp_path)(_request())

    assert not result.allowed
    assert "context window is unknown" in result.reason
    assert provider.requests == []


async def test_reviewer_timeout_is_a_denial_not_a_human_prompt(tmp_path: Path) -> None:
    class SlowProvider(FakeProvider):
        async def stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
        ) -> AsyncIterator[ProviderEvent]:
            await asyncio.sleep(10)
            yield FinalMessage("", "end_turn")

    result = await AutoReviewer(
        SlowProvider([]), workspace=tmp_path, timeout_seconds=0.001
    )(_request())
    assert not result.allowed
    assert "timed out" in result.reason


async def test_reviewer_denial_reaches_next_model_request_without_execution(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="w1",
                            name="write",
                            input={"path": "a.txt", "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("I will use a different approach."),
        ]
    )
    reviewer_provider = FakeProvider(
        [_text('{"allowed":false,"reason":"The task does not authorize this target."}')]
    )
    log = SessionLog.create(tmp_path / "sessions")
    await run(
        log,
        "Inspect only",
        provider=provider,
        tools=build_tools(tmp_path),
        budget=Budget(100_000),
        policy=WorkspacePolicy(tmp_path, mode="ask"),
        approve=AutoReviewer(reviewer_provider, workspace=tmp_path),
    )
    assert not (tmp_path / "a.txt").exists()
    feedback = [
        message["content"]
        for message in provider.requests[1]
        if message["role"] == "tool"
    ]
    assert "Reviewer rejected" in feedback[0]
    assert "does not authorize" in feedback[0]
    assert len(reviewer_provider.requests) == 1
    recorded = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert "Reviewer rejected" in str(recorded.data["artifact"])


async def test_build_does_not_invoke_reviewer_for_normal_edit(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="w1",
                            name="write",
                            input={"path": "a.txt", "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )
    reviewer_provider = FakeProvider([])
    await run(
        SessionLog.create(tmp_path / "sessions"),
        "Create a.txt",
        provider=provider,
        tools=build_tools(tmp_path),
        budget=Budget(100_000),
        policy=WorkspacePolicy(tmp_path),
        approve=AutoReviewer(reviewer_provider, workspace=tmp_path),
    )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x"
    assert reviewer_provider.requests == []
