"""统一工具授权、执行范围、超时和错误转换；调用方记录结果事实。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import jsonschema
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from cade.harness.cancellation import CancellationToken
from cade.harness.events import LoopEvent, ToolStartEvent, ToolUpdateEvent
from cade.harness.execution.scope import execution_scope
from cade.harness.permissions import (
    Approver,
    PermissionCheck,
    Policy,
    authorize,
    parent_authorization,
)
from cade.harness.session.types import JsonValue, ToolCallBlock
from cade.harness.tools import (
    Tool,
    ToolDismissed,
    ToolResult,
    current_tool_context,
    tool_context,
)

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
    tool = tools.get(call.name)
    if tool is not None:
        call = call.model_copy(
            update={"args": _optional_arguments(call.args, tool.parameters)}
        )
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
    if tool is None:
        return ToolExecution(
            ToolResult(f"Unknown tool: {call.name}", is_error=True), authorization
        )
    try:
        validator = validator_for(tool.parameters)
        validator.check_schema(tool.parameters)
        validator(tool.parameters, registry=Registry()).validate(call.args)
    except (jsonschema.SchemaError, jsonschema.ValidationError, Unresolvable) as exc:
        return ToolExecution(
            ToolResult(f"Tool argument schema error: {exc}", is_error=True),
            authorization,
        )
    if on_event is not None:
        on_event(ToolStartEvent(call.id, call.name, call.args))
    runtime = replace(
        current_tool_context(),
        on_update=(lambda text: on_event(ToolUpdateEvent(call.id, call.name, text)))
        if on_event is not None
        else None,
    )
    with (
        execution_scope(authorization.scope),
        parent_authorization(messages),
        tool_context(runtime),
    ):
        try:
            operation = _execute_cancellable(tool, call, cancel)
            result = (
                await asyncio.wait_for(operation, timeout=timeout_seconds)
                if timeout_seconds > 0 and not getattr(tool, "manages_timeout", False)
                else await operation
            )
        except ToolDismissed as exc:
            if cancel is not None:
                cancel.cancel(str(exc))
            result = ToolResult(str(exc), is_error=True)
        except TimeoutError:
            result = ToolResult(
                f"Tool timed out after {timeout_seconds:g}s: {call.name}",
                is_error=True,
            )
        # 第三方工具可能抛出任意库异常，执行边界将异常转换为工具结果。
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(str(exc), is_error=True)
    if len(result.content) > _MAX_TOOL_RESULT_CHARS:
        result = ToolResult(
            result.content[:_MAX_TOOL_RESULT_CHARS] + "\n[truncated]",
            result.is_error,
            result.metadata,
        )
    return ToolExecution(result, authorization)


def _optional_arguments(value: JsonValue, schema: dict[str, Any]) -> JsonValue:
    """把 strict Responses 用 null 表达的省略值还原为可选参数。"""
    if not isinstance(schema, dict):
        return value
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return value
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            field = properties.get(key, {})
            if not isinstance(field, dict):
                result[key] = item
                continue
            field_type = field.get("type")
            nullable = (
                field_type == "null"
                or (isinstance(field_type, list) and "null" in field_type)
                or any(part.get("type") == "null" for part in field.get("anyOf", []))
            )
            if (
                item is None
                and key in properties
                and key not in required
                and not nullable
            ):
                continue
            result[key] = _optional_arguments(item, field)
        return result
    if isinstance(value, list):
        return [_optional_arguments(item, schema.get("items", {})) for item in value]
    return value


async def _execute_cancellable(
    tool: Tool, call: ToolCallBlock, cancel: CancellationToken | None
) -> ToolResult:
    if cancel is None or getattr(tool, "handles_cancellation", False):
        return await tool.execute(call, cancel)
    if cancel.cancelled:
        return ToolResult("Operation aborted", is_error=True)
    task = asyncio.create_task(tool.execute(call, cancel))
    interrupted = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((task, interrupted), return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return await task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return ToolResult("Operation aborted", is_error=True)
    finally:
        task.cancel()
        interrupted.cancel()
        await asyncio.gather(task, interrupted, return_exceptions=True)
