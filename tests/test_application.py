"""应用装配在无终端条件下执行任务并保留日志与恢复记录。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.provider import ProviderConfig
from cade.coding.security.review import AutoReviewer
from cade.coding.subagents import DelegateTool
from cade.harness.cancellation import CancellationToken
from cade.harness.execution.scope import ExecutionScope, current_scope
from cade.harness.permissions import PermissionCheck
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult


class WritingProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallEvent(
                [
                    ToolCall(
                        "write-1", "write", {"path": "result.txt", "content": "done"}
                    )
                ]
            )
        else:
            yield TextDelta("finished")
            yield FinalMessage(content="finished", stop_reason="end_turn")


@pytest.mark.parametrize("interactive", [False, True])
def test_application_runs_without_terminal_and_records_recovery(
    tmp_path: Path, interactive: bool
) -> None:
    provider = WritingProvider()
    config = CadeConfig(sandbox="danger-full-access")
    with ExitStack() as resources:
        assembled = create_session(
            tmp_path,
            config,
            resources=resources,
            provider=provider,
            interactive=interactive,
            auto_approve=True,
        )
        session = assembled.session
        assert session.log.path.exists() is not interactive
        result = asyncio.run(
            session.run_task(
                "write the result", lambda event: None, approver=assembled.approver
            )
        )
        assert result.termination == "completed"
        assert result.answer == "finished"
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"
        entries = session.log.branch()
        assert len([entry for entry in entries if entry.type == "request"]) == 2
        assert len([entry for entry in entries if entry.type == "tool_result"]) == 1
        assert len(session.recovery.store.actions(session.log.session_id)) == 1


def test_direct_application_tool_uses_shared_execution_scope(tmp_path: Path) -> None:
    expected = ExecutionScope(True, (tmp_path / "approved",))

    class ScopedPolicy:
        mode = "build"

        def check(self, call: ToolCallBlock) -> PermissionCheck:
            return PermissionCheck("ask", "approved scope", expected)

    class ScopeTool:
        name = "scope"
        description = "Report scope"
        parameters: ClassVar[dict[str, Any]] = {}

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None = None
        ) -> ToolResult:
            assert current_scope() == expected
            return ToolResult("scoped")

    async def approve(call: ToolCallBlock) -> bool:
        return True

    with ExitStack() as resources:
        session = create_session(
            tmp_path, CadeConfig(), resources=resources, provider=WritingProvider()
        ).session
        session.policy = ScopedPolicy()  # type: ignore[assignment]
        session.base_tools = (ScopeTool(),)
        result = asyncio.run(session.execute_tool("scope", {}, approve))
        assert result == ToolResult("scoped")
        assert current_scope() == ExecutionScope()


def test_model_update_reaches_all_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = WritingProvider()
    selected = WritingProvider()
    selected.model = "gpt-5.5"
    selected.context_window = 1_050_000
    configurations: list[ProviderConfig] = []

    def build(config: ProviderConfig) -> WritingProvider:
        configurations.append(config)
        return selected

    monkeypatch.setattr("cade.app.session.build_provider", build)
    with ExitStack() as resources:
        service = create_session(
            tmp_path, CadeConfig(), resources=resources, provider=initial
        ).session
        reviewer = AutoReviewer(initial, workspace=tmp_path)
        service.automatic_approver = reviewer
        service.select_model(
            "openai/gpt-5.5", "medium", thinking=False, context_window=200_000
        )
        assert configurations[-1].thinking is False
        assert configurations[-1].context_window == 200_000
        assert service.provider is selected
        assert service.compactor.provider is selected
        assert reviewer.provider is selected
        assert service.budget.context_window == 200_000
        assert service.context_window_explicit
        runners = {
            tool.runner for tool in service.base_tools if isinstance(tool, DelegateTool)
        }
        assert runners
        assert all(
            runner.provider is selected and runner.budget == service.budget
            for runner in runners
        )
        asyncio.run(service.command("/effort high"))
        assert configurations[-1].context_window == 200_000
        assert service.budget.context_window == 200_000
        asyncio.run(service.command("/context-window default"))
        assert not service.context_window_explicit
        assert service.budget.context_window == 1_050_000
        assert service.provider_config.context_window is None
        assert all(runner.budget == service.budget for runner in runners)


@pytest.mark.parametrize("failure", ["effort", "window", "provider"])
def test_failed_model_selection_keeps_active_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    selected = WritingProvider()
    selected.model = "gpt-5.5"
    selected.context_window = 1_050_000

    def build(config: ProviderConfig) -> WritingProvider:
        if failure == "provider":
            raise RuntimeError("provider setup failed")
        return selected

    monkeypatch.setattr("cade.app.session.build_provider", build)
    with ExitStack() as resources:
        service = create_session(
            tmp_path, CadeConfig(), resources=resources, provider=WritingProvider()
        ).session
        original = (
            service.provider_config,
            service.provider,
            service.budget,
            service.context_window_explicit,
            service.compactor,
        )
        with pytest.raises((ValueError, RuntimeError)):
            service.select_model(
                "openai/gpt-5.5",
                "invalid" if failure == "effort" else "high",
                thinking=False,
                context_window=2_000_000 if failure == "window" else 200_000,
            )
        assert (
            service.provider_config,
            service.provider,
            service.budget,
            service.context_window_explicit,
            service.compactor,
        ) == original
        assert all(
            tool.runner.provider is service.provider
            and tool.runner.budget == service.budget
            for tool in service.base_tools
            if isinstance(tool, DelegateTool)
        )
