"""Cade Mini 的 agent 循环：请求、工具、事实追加。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Any, Concatenate, Literal, Protocol, cast

from cade_mini.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    ProviderReplayPayload,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    UsageUpdate,
)
from cade_mini.ai.types import StreamOptions, ToolDefinition
from cade_mini.core.log import SessionLog
from cade_mini.core.projection import project
from cade_mini.core.request import Budget, Request, build, estimate
from cade_mini.core.types import JsonValue, ToolCallBlock, ToolResultMessage

type Decision = Literal["allow", "deny", "ask"]
type Termination = Literal[
    "completed",
    "max_tokens",
    "step_limit",
    "context_error",
    "provider_error",
    "aborted",
]

_MAX_TOOL_RESULT_CHARS = 64_000


class Provider(Protocol):
    """循环所需的 provider 面；由 cade_mini.ai adapter 结构满足。"""

    @property
    def model(self) -> str: ...

    @property
    def transport(self) -> str: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]: ...


@dataclass(frozen=True)
class ToolResult:
    """工具返回值；正文会进入 tool_result 事实。"""

    content: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


class Tool(Protocol):
    """循环可执行的工具。"""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None,
    ) -> ToolResult: ...


class Policy(Protocol):
    """工具调用前的允许/拒绝/询问决策。"""

    def check(self, call: ToolCallBlock) -> Decision: ...


class Hooks(Protocol):
    """工具生命周期 hooks。"""

    async def before(self, call: ToolCallBlock) -> tuple[bool, str]: ...

    async def after(
        self,
        call: ToolCallBlock,
        content: str,
        is_error: bool,
    ) -> None: ...


class CancellationToken:
    """协作式取消；循环在步骤和工具边界检查。"""

    def __init__(self) -> None:
        self._cancelled = False
        self.reason = "cancelled"

    def cancel(self, reason: str = "cancelled") -> None:
        self._cancelled = True
        self.reason = reason

    @property
    def cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True)
class TextDeltaEvent:
    text: str


@dataclass(frozen=True)
class ReasoningDeltaEvent:
    text: str


@dataclass(frozen=True)
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ToolStartEvent:
    call_id: str
    name: str
    args: dict[str, JsonValue]


@dataclass(frozen=True)
class ToolEndEvent:
    call_id: str
    content: str
    is_error: bool


type LoopEvent = (
    TextDeltaEvent | ReasoningDeltaEvent | UsageEvent | ToolStartEvent | ToolEndEvent
)


@dataclass(frozen=True)
class RunResult:
    answer: str
    steps: int
    termination: Termination
    error: str | None = None
    usage: dict[str, int] | None = None


def _exclusive_session[**P](
    operation: Callable[Concatenate[SessionLog, P], Awaitable[RunResult]],
) -> Callable[Concatenate[SessionLog, P], Coroutine[Any, Any, RunResult]]:
    """在恢复、模型调用和工具执行期间持有会话运行锁。"""

    @wraps(operation)
    async def locked(log: SessionLog, *args: P.args, **kwargs: P.kwargs) -> RunResult:
        with log.run_lock():
            return await operation(log, *args, **kwargs)

    return locked


@_exclusive_session
async def run(
    log: SessionLog,
    text: str,
    *,
    provider: Provider,
    tools: Sequence[Tool],
    budget: Budget,
    policy: Policy | None = None,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]] | None = None,
    options: StreamOptions | None = None,
    context: Sequence[str] = (),
    max_steps: int = 50,
    tool_timeout_seconds: float = 120.0,
    max_provider_retries: int = 1,
    retry_delay_seconds: float = 0.5,
    cancel: CancellationToken | None = None,
    on_event: Callable[[LoopEvent], None] | None = None,
    compact: Callable[[SessionLog, Budget], Awaitable[bool]] | None = None,
    window: Callable[[SessionLog], bool] | None = None,
    hooks: Hooks | None = None,
    steering: Callable[[], str | None] | None = None,
) -> RunResult:
    """追加用户输入并驱动模型/工具循环，直到完成或终止。

    每次 provider 调用前把请求审计写入日志；只有 assistant 与 tool_result
    进入投影。上下文超限时先调用 compact 钩子；压缩后仍超预算时再调用
    window 钩子丢弃更早的历史。
    用量汇总包含本次运行的摘要调用；失败尝试保留已上报的用量。
    """
    if max_steps <= 0:
        raise ValueError("max_steps must be positive")
    tools_by_name = _tool_map(tools)
    definitions = tuple(
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
        )
        for tool in tools
    )

    _recover_tool_calls(log)
    log.append("user", {"text": text, "source": "user"})
    total_usage = {"input": 0, "output": 0}
    has_usage = False
    last_answer = ""
    retries = 0

    for step in range(1, max_steps + 1):
        if cancel is not None and cancel.cancelled:
            return _finish(
                log,
                "aborted",
                step,
                last_answer,
                _usage(total_usage, has_usage),
                cancel.reason,
            )

        if steering is not None:
            while True:
                message = steering()
                if message is None:
                    break
                if message.strip():
                    log.append(
                        "user",
                        {"text": message.strip(), "source": "runtime"},
                    )

        entries = log.branch()
        conversation = project(entries)
        full_tokens = estimate(conversation, tools=definitions, context=context)
        if full_tokens > budget.input_budget and compact is not None:
            compacted = await compact(log, budget)
            updated_entries = log.branch()
            # 只统计本次压缩新增的调用，失败摘要也可能已产生用量。
            for entry in updated_entries[len(entries) :]:
                usage = entry.data.get("usage")
                if entry.type in {"compaction", "attempt"} and isinstance(usage, dict):
                    total_usage["input"] += cast(int, usage["input"])
                    total_usage["output"] += cast(int, usage["output"])
                    has_usage = True
            if compacted:
                conversation = project(updated_entries)
        request = build(
            conversation,
            tools=definitions,
            budget=budget,
            options=options,
            context=context,
        )
        if request.report.over_budget and window is not None and window(log):
            conversation = project(log.branch())
            request = build(
                conversation,
                tools=definitions,
                budget=budget,
                options=options,
                context=context,
            )
        if request.report.over_budget:
            return _finish(
                log,
                "context_error",
                step,
                last_answer,
                _usage(total_usage, has_usage),
                (
                    f"request needs {request.report.estimated_tokens} tokens; "
                    f"budget is {request.report.budget_tokens}"
                ),
            )

        _append_request(log, provider, request, context)
        result = await _call_provider(provider, request, cancel, on_event)

        if result.usage is not None:
            total_usage["input"] += result.usage["input"]
            total_usage["output"] += result.usage["output"]
            has_usage = True

        if result.cancelled:
            reason = cancel.reason if cancel is not None else "cancelled"
            return _finish(
                log,
                "aborted",
                step,
                last_answer,
                _usage(total_usage, has_usage),
                reason,
            )

        if result.failure is not None:
            _append_attempt(log, provider, result.failure, result.usage)
            if retries < max_provider_retries and _retryable(result.failure):
                retries += 1
                if retry_delay_seconds > 0:
                    await asyncio.sleep(retry_delay_seconds)
                continue
            return _finish(
                log,
                "provider_error",
                step,
                last_answer,
                _usage(total_usage, has_usage),
                result.failure.message,
            )

        if not result.text and not result.calls:
            failure = ProviderFailure(
                message="empty model response",
                exception_type="EmptyResponse",
            )
            _append_attempt(log, provider, failure, result.usage)
            return _finish(
                log,
                "provider_error",
                step,
                last_answer,
                _usage(total_usage, has_usage),
                failure.message,
            )

        _append_assistant(log, provider, result)
        retries = 0
        last_answer = result.text or last_answer

        if not result.calls:
            termination: Termination = (
                "max_tokens" if result.stop_reason == "max_tokens" else "completed"
            )
            return _finish(
                log,
                termination,
                step,
                last_answer,
                _usage(total_usage, has_usage),
            )

        for index, call in enumerate(result.calls):
            if cancel is not None and cancel.cancelled:
                for remaining in result.calls[index:]:
                    _append_tool_result(
                        log,
                        remaining,
                        "Tool execution was cancelled.",
                        True,
                    )
                return _finish(
                    log,
                    "aborted",
                    step,
                    last_answer,
                    _usage(total_usage, has_usage),
                    cancel.reason,
                )

            # Show the call while permission is pending, including denied calls.
            _emit(on_event, ToolStartEvent(call.id, call.name, call.args))
            decision = await _decide(policy, approve, call)
            if decision == "deny":
                content = f"Permission denied: {call.name}"
                _append_tool_result(log, call, content, True)
                _emit(on_event, ToolEndEvent(call.id, content, True))
                continue

            tool = tools_by_name.get(call.name)
            if tool is None:
                content = f"Unknown tool: {call.name}"
                _append_tool_result(log, call, content, True)
                _emit(on_event, ToolEndEvent(call.id, content, True))
                continue

            if hooks is not None:
                allowed, hook_output = await hooks.before(call)
                if not allowed:
                    content = f"Hook denied {call.name}: {hook_output}".strip()
                    _append_tool_result(log, call, content, True)
                    _emit(on_event, ToolEndEvent(call.id, content, True))
                    continue

            tool_result = await _execute_tool(tool, call, cancel, tool_timeout_seconds)
            content = _truncate(tool_result.content)
            _append_tool_result(log, call, content, tool_result.is_error)
            _emit(on_event, ToolEndEvent(call.id, content, tool_result.is_error))
            if hooks is not None:
                await hooks.after(call, content, tool_result.is_error)

    return _finish(
        log,
        "step_limit",
        max_steps,
        last_answer,
        _usage(total_usage, has_usage),
    )


def _recover_tool_calls(log: SessionLog) -> None:
    """在新输入前持久化末尾的中断结果，不重试未知副作用。"""
    branch = log.branch()
    messages = project(branch)
    completed = {
        call_id
        for entry in branch
        if entry.type == "tool_result"
        and isinstance(call_id := entry.data.get("call_id"), str)
    }
    for message in messages:
        if isinstance(message, ToolResultMessage) and message.call_id not in completed:
            log.append(
                "tool_result",
                {
                    "call_id": message.call_id,
                    "status": "interrupted",
                    "content": (
                        "Execution outcome unknown after process interruption. "
                        "Inspect workspace before retrying."
                    ),
                },
            )


async def _execute_tool(
    tool: Tool,
    call: ToolCallBlock,
    cancel: CancellationToken | None,
    timeout_seconds: float,
) -> ToolResult:
    try:
        execution = tool.execute(call, cancel)
        if timeout_seconds > 0:
            return await asyncio.wait_for(execution, timeout=timeout_seconds)
        return await execution
    except TimeoutError:
        return ToolResult(
            content=f"Tool timed out after {timeout_seconds:g}s: {call.name}",
            is_error=True,
        )
    # 第三方工具可能抛出任意库异常；循环必须把它变成可回复的工具错误。
    except Exception as exc:  # noqa: BLE001
        return ToolResult(content=f"Tool error: {exc}", is_error=True)


@dataclass(frozen=True)
class _ProviderResult:
    text: str
    calls: tuple[ToolCallBlock, ...]
    usage: dict[str, int] | None
    provider_state: dict[str, JsonValue] | None
    stop_reason: str
    failure: ProviderFailure | None = None
    cancelled: bool = False


async def _call_provider(
    provider: Provider,
    request: Request,
    cancel: CancellationToken | None,
    on_event: Callable[[LoopEvent], None] | None,
) -> _ProviderResult:
    text_parts: list[str] = []
    calls: list[ToolCallBlock] = []
    usage: dict[str, int] | None = None
    replay: ProviderReplayPayload | None = None
    stop_reason = "end_turn"
    failure: ProviderFailure | None = None

    try:
        async for event in provider.stream(
            list(request.messages),
            list(request.tools),
            request.options,
        ):
            if cancel is not None and cancel.cancelled:
                return _ProviderResult(
                    "",
                    (),
                    usage,
                    None,
                    stop_reason,
                    cancelled=True,
                )
            if isinstance(event, TextDelta):
                text_parts.append(event.chunk)
                _emit(on_event, TextDeltaEvent(event.chunk))
            elif isinstance(event, ToolCallEvent):
                for call in event.calls:
                    calls.append(
                        ToolCallBlock(
                            id=call.id,
                            name=call.name,
                            args=cast(dict[str, JsonValue], dict(call.input)),
                        )
                    )
            elif isinstance(event, ReasoningDelta):
                _emit(on_event, ReasoningDeltaEvent(event.chunk))
            elif isinstance(event, UsageUpdate):
                usage = {
                    "input": event.input_tokens,
                    "output": event.output_tokens,
                }
                _emit(on_event, UsageEvent(event.input_tokens, event.output_tokens))
            elif isinstance(event, FinalMessage):
                stop_reason = event.stop_reason
                replay = event.replay_payload
                if not text_parts and event.content:
                    text_parts.append(event.content)
                    _emit(on_event, TextDeltaEvent(event.content))
            elif isinstance(event, ProviderFailure):
                failure = event
    except Exception as exc:  # noqa: BLE001
        # Provider 适配器可能抛出供应商库异常；统一转成可重试的失败事实。
        failure = ProviderFailure(
            message=str(exc),
            exception_type=type(exc).__name__,
        )

    text = "".join(text_parts)
    if failure is None and stop_reason == "error":
        failure = ProviderFailure(
            message=text or "provider error",
            exception_type="ProviderError",
        )
    return _ProviderResult(
        text=text,
        calls=tuple(calls),
        usage=usage,
        provider_state=_provider_state(replay),
        stop_reason=stop_reason,
        failure=failure,
        cancelled=stop_reason in {"aborted", "cancelled"},
    )


def _provider_state(
    replay: ProviderReplayPayload | None,
) -> dict[str, JsonValue] | None:
    if replay is None:
        return None
    return cast(
        dict[str, JsonValue],
        {
            "protocol": replay.protocol,
            "transport": replay.transport,
            "model": replay.model,
            "output_items": [dict(item) for item in replay.output_items],
            "reasoning_tokens": replay.reasoning_tokens,
            "output_tokens": replay.output_tokens,
            "output_token_limit": replay.output_token_limit,
        },
    )


def _append_request(
    log: SessionLog,
    provider: Provider,
    request: Request,
    context: Sequence[str],
) -> None:
    dropped = list(request.report.dropped_tool_calls)
    dropped.extend(f"turn:{index + 1}" for index in range(request.report.dropped_turns))
    log.append(
        "request",
        {
            "model": provider.model,
            "provider": provider.transport,
            "tools_digest": request.tools_digest,
            "conversation_digest": request.conversation_digest,
            "context": cast(list[JsonValue], list(context)),
            "estimated_tokens": request.report.estimated_tokens,
            "dropped": cast(list[JsonValue], dropped),
            "snapshot": request.as_record(),
        },
    )


def _append_attempt(
    log: SessionLog,
    provider: Provider,
    failure: ProviderFailure,
    usage: dict[str, int] | None = None,
) -> None:
    log.append(
        "attempt",
        {
            "model": provider.model,
            "provider": provider.transport,
            "error": failure.message,
            "usage": cast(JsonValue, usage),
        },
    )


def _append_assistant(
    log: SessionLog,
    provider: Provider,
    result: _ProviderResult,
) -> None:
    blocks: list[JsonValue] = []
    if result.text:
        blocks.append({"type": "text", "text": result.text})
    for call in result.calls:
        blocks.append(
            {
                "type": "tool_call",
                "id": call.id,
                "name": call.name,
                "args": call.args,
            }
        )
    log.append(
        "assistant",
        {
            "blocks": blocks,
            "model": provider.model,
            "provider": provider.transport,
            "usage": cast(JsonValue, result.usage),
            "provider_state": result.provider_state,
        },
    )


def _append_tool_result(
    log: SessionLog,
    call: ToolCallBlock,
    content: str,
    is_error: bool,
) -> None:
    log.append(
        "tool_result",
        {
            "call_id": call.id,
            "status": "error" if is_error else "ok",
            "content": content,
        },
    )


def _finish(
    log: SessionLog,
    termination: Termination,
    steps: int,
    answer: str,
    usage: dict[str, int] | None,
    error: str | None = None,
) -> RunResult:
    log.append(
        "run",
        {
            "termination": termination,
            "steps": steps,
            "usage": cast(JsonValue, usage),
            "error": error,
        },
    )
    return RunResult(
        answer=answer,
        steps=steps,
        termination=termination,
        error=error,
        usage=usage,
    )


def _tool_map(tools: Sequence[Tool]) -> dict[str, Tool]:
    result: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in result:
            raise ValueError(f"duplicate tool name: {tool.name}")
        result[tool.name] = tool
    return result


async def _decide(
    policy: Policy | None,
    approve: Callable[[ToolCallBlock], bool | Awaitable[bool]] | None,
    call: ToolCallBlock,
) -> Decision:
    decision = policy.check(call) if policy is not None else "allow"
    if decision != "ask":
        return decision
    if approve is None:
        return "deny"
    result = approve(call)
    if inspect.isawaitable(result):
        result = await result
    return "allow" if result else "deny"


def _retryable(failure: ProviderFailure) -> bool:
    status = failure.status_code
    if status in {400, 401, 403, 404, 413, 422}:
        return False
    if status in {429, 500, 502, 503, 529}:
        return True
    text = f"{failure.exception_type} {failure.message}".lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "connection",
            "rate limit",
            "temporary",
            "429",
            "500",
            "502",
            "503",
        )
    )


def _truncate(content: str) -> str:
    if len(content) <= _MAX_TOOL_RESULT_CHARS:
        return content
    return content[:_MAX_TOOL_RESULT_CHARS] + "\n[truncated]"


def _usage(total: dict[str, int], has_usage: bool) -> dict[str, int] | None:
    return dict(total) if has_usage else None


def _emit(
    on_event: Callable[[LoopEvent], None] | None,
    event: LoopEvent,
) -> None:
    if on_event is not None:
        on_event(event)
