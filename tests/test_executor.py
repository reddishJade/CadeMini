"""共享工具执行入口的授权、范围和失败处理。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cade.harness.cancellation import CancellationToken
from cade.harness.events import LoopEvent, ToolStartEvent
from cade.harness.execution.scope import ExecutionScope, current_scope
from cade.harness.executor import execute_tool
from cade.harness.permissions import ApprovalResult, PermissionCheck
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult


class ScopeTool:
    name = "scope"
    description = "Report the execution scope"
    parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self) -> None:
        self.scopes: list[ExecutionScope] = []

    async def execute(
        self, call: ToolCallBlock, signal: CancellationToken | None
    ) -> ToolResult:
        await asyncio.sleep(0)
        self.scopes.append(current_scope())
        return ToolResult("executed", metadata={"retained": True})


async def test_executor_applies_approved_scope_and_restores_it(tmp_path: Path) -> None:
    expected = ExecutionScope(True, (tmp_path / "allowed",))

    class Policy:
        def check(self, call: ToolCallBlock) -> PermissionCheck:
            return PermissionCheck("ask", "external write", expected)

    tool = ScopeTool()
    events: list[LoopEvent] = []
    result = await execute_tool(
        ToolCallBlock(id="call", name="scope", args={}),
        {"scope": tool},
        policy=Policy(),
        approve=lambda request: ApprovalResult(True, "approved"),
        on_event=events.append,
    )
    assert result.authorization.scope == expected
    assert result.result.metadata == {"retained": True}
    assert tool.scopes == [expected]
    assert current_scope() == ExecutionScope()
    assert events == [ToolStartEvent("call", "scope", {})]


async def test_executor_denial_retains_reason_and_never_starts_tool() -> None:
    class Policy:
        def check(self, call: ToolCallBlock) -> PermissionCheck:
            return PermissionCheck("ask", "needs approval")

    tool = ScopeTool()
    events: list[LoopEvent] = []
    result = await execute_tool(
        ToolCallBlock(id="call", name="scope", args={}),
        {"scope": tool},
        policy=Policy(),
        approve=lambda request: ApprovalResult(False, "review rejected"),
        on_event=events.append,
    )
    assert result.result.is_error
    assert "review rejected" in result.result.content
    assert tool.scopes == []
    assert events == []


@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_executor_converts_tool_failures(failure: str) -> None:
    class FailingTool(ScopeTool):
        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None
        ) -> ToolResult:
            if failure == "timeout":
                await asyncio.sleep(10)
            raise ValueError("invalid tool operation")

    result = await execute_tool(
        ToolCallBlock(id="call", name="scope", args={}),
        {"scope": FailingTool()},
        timeout_seconds=0.01,
    )
    assert result.result.is_error
    assert ("timed out" if failure == "timeout" else "invalid tool operation") in (
        result.result.content
    )
    assert current_scope() == ExecutionScope()
