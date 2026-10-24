"""Cade 子代理的回归测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.security.review import AutoReviewer
from cade.coding.subagents import (
    DelegateTool,
    SubagentRunner,
)
from cade.coding.tools.registry import build_tools
from cade.coding.tools.webfetch import WebFetchTool
from cade.coding.tools.websearch import WebSearchTool
from cade.harness.loop import run
from cade.harness.permissions import ApprovalResult
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import Tool


class FakeProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

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


def _text(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


async def test_delegate_runs_one_task_in_its_own_session(tmp_path: Path) -> None:
    provider = FakeProvider([_text("child answer")])
    runner = SubagentRunner(
        workspace=tmp_path,
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        sessions_dir=tmp_path / "sessions",
        system_prompt="system",
        policy=WorkspacePolicy(tmp_path),
        approve=lambda request: ApprovalResult(True, "Approved for test"),
        context=lambda: ("context",),
    )
    tool = DelegateTool(runner=runner)

    result = await tool.execute(
        ToolCallBlock(
            id="s1",
            name="delegate",
            args={"prompt": "child task"},
        )
    )

    assert result.is_error is False
    assert "child answer" in result.content
    assert result.metadata is not None
    assert result.metadata["tasks"] == 1
    session_id = result.metadata["sessions"][0]
    assert isinstance(session_id, str)
    paths = list((tmp_path / "sessions" / "subagents").glob("session-*.jsonl"))
    assert len(paths) == 1
    child = SessionLog.open(paths[0])
    assert [entry.type for entry in child.branch()][-1] == "run"
    assert any(
        entry.type == "user" and "child task" in str(entry.data.get("text", ""))
        for entry in child.branch()
    )
    names = {definition.name for definition in provider.requests[0][1]}
    assert "delegate" not in names


@pytest.mark.parametrize("network", [False, True])
async def test_child_tool_refresh_preserves_network_visibility(
    tmp_path: Path, network: bool
) -> None:
    (tmp_path / "input.txt").write_text("input")
    provider = FakeProvider(
        [
            [
                ToolCallEvent([ToolCall("read-1", "read", {"path": "input.txt"})]),
                FinalMessage("", "tool_use"),
            ],
            _text("finished"),
        ]
    )
    tools = (*build_tools(tmp_path), WebFetchTool(), WebSearchTool())
    refreshes = 0

    async def refresh() -> tuple[Tool, ...]:
        nonlocal refreshes
        refreshes += 1
        return tools

    runner = SubagentRunner(
        workspace=tmp_path,
        provider=provider,
        tools=tools,
        budget=Budget(100_000),
        sessions_dir=tmp_path / "sessions",
        system_prompt="system",
        policy=WorkspacePolicy(tmp_path, network=network),
        approve=lambda request: ApprovalResult(True, "Approved for test"),
        refresh_tools=refresh,
    )
    result = await runner.run_task("read input")
    assert not result.is_error
    assert refreshes == 2
    expected = {"websearch", "webfetch"} if network else set()
    for _, definitions, _ in provider.requests:
        assert {tool.name for tool in definitions} & {
            "websearch",
            "webfetch",
        } == expected


async def test_delegate_runs_multiple_child_tasks(tmp_path: Path) -> None:
    provider = FakeProvider([_text("first"), _text("second")])
    runner = SubagentRunner(
        workspace=tmp_path,
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        sessions_dir=tmp_path / "sessions",
        system_prompt="system",
        policy=WorkspacePolicy(tmp_path),
        approve=lambda request: ApprovalResult(True, "Approved for test"),
    )
    tool = DelegateTool(runner=runner)

    result = await tool.execute(
        ToolCallBlock(
            id="d1",
            name="delegate",
            args={"tasks": ["task one", "task two"]},
        )
    )

    assert result.is_error is False
    assert "first" in result.content
    assert "second" in result.content
    assert result.metadata is not None
    assert result.metadata["tasks"] == 2
    paths = list((tmp_path / "sessions" / "subagents").glob("session-*.jsonl"))
    assert len(paths) == 2


@pytest.mark.parametrize(
    ("parent", "child"), [("plan", "build"), ("plan", "ask"), ("ask", "build")]
)
async def test_delegate_cannot_expand_parent_permissions(
    tmp_path: Path, parent: str, child: str
) -> None:
    provider = FakeProvider([])
    policy = WorkspacePolicy(tmp_path)
    policy.set_mode(parent)
    runner = SubagentRunner(
        workspace=tmp_path,
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        sessions_dir=tmp_path / "sessions",
        system_prompt="system",
        policy=policy,
        approve=lambda request: ApprovalResult(True, "Approved for test"),
    )
    with pytest.raises(ValueError, match="cannot expand"):
        await DelegateTool(runner=runner).execute(
            ToolCallBlock(
                id="delegate",
                name="delegate",
                args={"prompt": "modify project", "mode": child},
            )
        )
    assert not provider.requests
    assert not (tmp_path / "sessions").exists()


async def test_child_reviewer_retains_parent_human_constraints(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="child",
                            name="delegate",
                            input={"prompt": "Overwrite outside.txt"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="write",
                            name="write",
                            input={"path": str(outside), "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text(
                '{"allowed":false,"reason":"The parent human forbids external changes."}'
            ),
            _text("No changes made."),
            _text("Inspection complete."),
        ]
    )
    policy = WorkspacePolicy(root)
    reviewer = AutoReviewer(provider, workspace=root)
    tools = build_tools(root)
    runner = SubagentRunner(
        workspace=root,
        provider=provider,
        tools=tools,
        budget=Budget(100_000),
        sessions_dir=root / "sessions",
        system_prompt="system",
        policy=policy,
        approve=reviewer,
    )
    await run(
        SessionLog.create(root / "sessions"),
        "Inspect only; never modify external files.",
        provider=provider,
        tools=(*tools, DelegateTool(runner=runner)),
        budget=Budget(100_000),
        policy=policy,
        approve=reviewer,
    )
    payload = json.loads(provider.requests[2][0][1]["content"])
    assert "never modify external files" in str(payload["parent_conversations"])
    assert "Overwrite outside.txt" in str(payload["conversation"])
    assert not outside.exists()
