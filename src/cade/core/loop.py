"""Cade 的 agent 循环：请求、工具、事实追加。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import wraps
from threading import Lock
from typing import Any, Concatenate, Literal, Protocol, cast

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    ProviderReplayPayload,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.core.log import SessionLog
from cade.core.permissions import (
    Approver,
    PermissionCheck,
    Policy,
    authorize,
    parent_authorization,
)
from cade.core.projection import project
from cade.core.request import Budget, Request, build, estimate
from cade.core.types import JsonValue, LogEntry, ToolCallBlock, ToolResultMessage
from cade.execution_env.sandbox import execution_scope

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
    """循环所需的 provider 面；由 cade.ai adapter 结构满足。"""

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


class TurnLifecycle(Protocol):
    """宿主提供轮次检查点；核心不依赖工作区存储。"""

    def record(self, log: SessionLog) -> AbstractContextManager[None]: ...

    def started(self, log: SessionLog, user: LogEntry) -> None: ...

    def request_context(self, log: SessionLog) -> Sequence[str]: ...


class CancellationToken:
    """跨线程通知取消；同时唤醒等待中的 provider 请求。"""

    def __init__(self) -> None:
        self._cancelled = False
        self.reason = "cancelled"
        self._lock = Lock()
        self._waiters: set[asyncio.Future[None]] = set()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._cancelled:
                return
            self.reason = reason
            self._cancelled = True
            for waiter in self._waiters:
                waiter.get_loop().call_soon_threadsafe(self._wake, waiter)

    @staticmethod
    def _wake(waiter: asyncio.Future[None]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    async def wait(self) -> None:
        """等待取消通知，结束时移除订阅，避免遗留后台线程。"""
        waiter = asyncio.get_running_loop().create_future()
        with self._lock:
            if self._cancelled:
                return
            self._waiters.add(waiter)
        try:
            await waiter
        finally:
            with self._lock:
                self._waiters.discard(waiter)

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled


@dataclass(frozen=True)
class TextDeltaEvent:
    text: str


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


@dataclass(frozen=True)
class ReasoningDeltaEvent:
    """推理摘要增量；只用于宿主展示，不进入 API 投影。"""

    text: str


@dataclass(frozen=True)
class UsageEvent:
    """一次 provider 调用的累计用量，供宿主实时更新状态栏。"""

    input_tokens: int
    output_tokens: int


type LoopEvent = (
    TextDeltaEvent | ToolStartEvent | ToolEndEvent | ReasoningDeltaEvent | UsageEvent
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
        lifecycle = cast(TurnLifecycle | None, kwargs.get("lifecycle"))
        with (
            log.run_lock(),
            lifecycle.record(log) if lifecycle is not None else nullcontext(),
        ):
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
    approve: Approver | None = None,
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
    steering: Callable[[], str | None] | None = None,
    lifecycle: TurnLifecycle | None = None,
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
    user = log.append("user", {"text": text, "source": "user"})
    if lifecycle is not None:
        lifecycle.started(log, user)
        context = (*context, *lifecycle.request_context(log))
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
            if result.text:
                _append_assistant(log, provider, result)
                last_answer = result.text
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

            authorization = await authorize(
                call, policy, approve, context=context, messages=request.messages
            )
            if authorization.decision == "deny":
                content = f"Permission denied: {call.name}. {authorization.reason}"
                _append_tool_result(
                    log, call, content, True, authorization=authorization
                )
                _emit(on_event, ToolEndEvent(call.id, content, True))
                continue

            tool = tools_by_name.get(call.name)
            if tool is None:
                content = f"Unknown tool: {call.name}"
                _append_tool_result(
                    log, call, content, True, authorization=authorization
                )
                _emit(on_event, ToolEndEvent(call.id, content, True))
                continue

            _emit(on_event, ToolStartEvent(call.id, call.name, call.args))
            with (
                execution_scope(authorization.scope),
                parent_authorization(request.messages),
            ):
                tool_result = await _execute_tool(
                    tool, call, cancel, tool_timeout_seconds
                )
            content = _truncate(tool_result.content)
            _append_tool_result(
                log, call, content, tool_result.is_error, authorization=authorization
            )
            _emit(on_event, ToolEndEvent(call.id, content, tool_result.is_error))

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
    if cancel is None:
        return await _consume_provider(provider, request, cancel, on_event)
    if cancel.cancelled:
        return _ProviderResult("", (), None, None, "aborted", cancelled=True)
    stream = asyncio.create_task(_consume_provider(provider, request, cancel, on_event))
    interrupted = asyncio.create_task(cancel.wait())
    try:
        await asyncio.wait((stream, interrupted), return_when=asyncio.FIRST_COMPLETED)
        if cancel.cancelled:
            stream.cancel()
        return await stream
    except asyncio.CancelledError:
        if not cancel.cancelled:
            raise
        return _ProviderResult("", (), None, None, "aborted", cancelled=True)
    finally:
        stream.cancel()
        interrupted.cancel()
        await asyncio.gather(stream, interrupted, return_exceptions=True)


async def _consume_provider(
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
            elif isinstance(event, ReasoningDelta):
                _emit(on_event, ReasoningDeltaEvent(event.chunk))
            elif isinstance(event, ToolCallEvent):
                for call in event.calls:
                    calls.append(
                        ToolCallBlock(
                            id=call.id,
                            name=call.name,
                            args=cast(dict[str, JsonValue], dict(call.input)),
                        )
                    )
            elif isinstance(event, UsageUpdate):
                usage = {
                    "input": event.input_tokens,
                    "output": event.output_tokens,
                }
                _emit(
                    on_event,
                    UsageEvent(
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                    ),
                )
            elif isinstance(event, FinalMessage):
                stop_reason = event.stop_reason
                replay = event.replay_payload
                if not text_parts and event.content:
                    text_parts.append(event.content)
                    _emit(on_event, TextDeltaEvent(event.content))
            elif isinstance(event, ProviderFailure):
                failure = event
    except asyncio.CancelledError:
        if cancel is None or not cancel.cancelled:
            raise
        return _ProviderResult(
            "".join(text_parts), (), usage, None, "aborted", cancelled=True
        )
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
    *,
    authorization: PermissionCheck | None = None,
) -> None:
    data: dict[str, JsonValue] = {
        "call_id": call.id,
        "status": "error" if is_error else "ok",
        "content": content,
    }
    if authorization is not None:
        data["artifact"] = {
            "authorization": {
                "decision": authorization.decision,
                "reason": authorization.reason,
                "read_only": authorization.scope.read_only,
                "writable_paths": [
                    str(path) for path in authorization.scope.writable_paths
                ],
            }
        }
    log.append("tool_result", data)


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
