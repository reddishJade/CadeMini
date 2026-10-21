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
