"""统一工具授权、执行范围、超时和错误转换；调用方记录结果事实。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cade.harness.cancellation import CancellationToken
from cade.harness.events import LoopEvent, ToolStartEvent
from cade.harness.execution.scope import execution_scope
from cade.harness.permissions import (
    Approver,
    PermissionCheck,
    Policy,
    authorize,
    parent_authorization,
)
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import Tool, ToolResult

_MAX_TOOL_RESULT_CHARS = 64_000


@dataclass(frozen=True)
class ToolExecution:
    result: ToolResult
    authorization: PermissionCheck


def index_tools(tools: Sequence[Tool]) -> dict[str, Tool]:
    """验证工具名称唯一，并建立查找表。"""
    result: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in result:
            raise ValueError(f"duplicate tool name: {tool.name}")
        result[tool.name] = tool
    return result


async def execute_tool(
    call: ToolCallBlock,
    tools: Mapping[str, Tool],
    *,
    policy: Policy | None = None,
    approve: Approver | None = None,
    context: Sequence[str] = (),
    messages: Sequence[dict[str, Any]] = (),
    cancel: CancellationToken | None = None,
    timeout_seconds: float = 120.0,
    on_event: Callable[[LoopEvent], None] | None = None,
) -> ToolExecution:
    """执行一个调用；完成事件由调用方在结果提交后发出。"""
    authorization = await authorize(
        call, policy, approve, context=context, messages=messages
    )
    if authorization.decision == "deny":
        return ToolExecution(
            ToolResult(
                f"Permission denied: {call.name}. {authorization.reason}",
                is_error=True,
            ),
            authorization,
        )
    tool = tools.get(call.name)
    if tool is None:
        return ToolExecution(
            ToolResult(f"Unknown tool: {call.name}", is_error=True), authorization
        )
    if on_event is not None:
        on_event(ToolStartEvent(call.id, call.name, call.args))
    with execution_scope(authorization.scope), parent_authorization(messages):
        try:
            operation = tool.execute(call, cancel)
            result = (
                await asyncio.wait_for(operation, timeout=timeout_seconds)
                if timeout_seconds > 0
                else await operation
            )
        except TimeoutError:
            result = ToolResult(
                f"Tool timed out after {timeout_seconds:g}s: {call.name}",
                is_error=True,
            )
        # 第三方工具可能抛出任意库异常，执行边界将异常转换为工具结果。
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(f"Tool error: {exc}", is_error=True)
    if len(result.content) > _MAX_TOOL_RESULT_CHARS:
        result = ToolResult(
            result.content[:_MAX_TOOL_RESULT_CHARS] + "\n[truncated]",
            result.is_error,
            result.metadata,
        )
    return ToolExecution(result, authorization)
