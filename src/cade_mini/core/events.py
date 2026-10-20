"""持久事件的写入契约；跨事件配对仍由投影校验。"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from cade_mini.ai.types import StreamOptions
from cade_mini.core.types import ContentBlock, EventType, JsonValue, ToolCallBlock

type NonEmpty = Annotated[str, Field(min_length=1)]
type Count = Annotated[int, Field(ge=0)]

_OPTIONS = TypeAdapter(StreamOptions)
_OPTION_NAMES = frozenset(field.name for field in fields(StreamOptions))


class _Payload(BaseModel):
    """拒绝额外字段、隐式类型转换和非有限数字。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class _Usage(_Payload):
    input: Count
    output: Count


class _Meta(_Payload):
    session: NonEmpty
    project: NonEmpty
    created: NonEmpty


class _System(_Payload):
    text: str


class _User(_System):
    source: Literal["user", "runtime"] = "user"


class _Assistant(_Payload):
    blocks: list[ContentBlock]
    model: str | None = None
    provider: str | None = None
    usage: _Usage | None = None
    provider_state: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def valid_calls(self) -> _Assistant:
        seen: set[str] = set()
        for block in self.blocks:
            if isinstance(block, ToolCallBlock):
                if not block.id or not block.name or block.id in seen:
                    raise ValueError("tool calls need unique non-empty ids and names")
                seen.add(block.id)
        return self


class _ToolResult(_Payload):
    call_id: NonEmpty
    status: Literal["ok", "error", "interrupted"]
    content: str
    artifact: dict[str, JsonValue] | None = None


class _Compaction(_Payload):
    summary: str
    first_kept_id: NonEmpty
    tokens_before: Count
    model: str
    provider: str
    usage: _Usage | None = None
    details: dict[str, JsonValue] | None = None


class _Window(_Payload):
    start_id: NonEmpty
    reason: Literal["manual", "fallback"]
    handoff: str | None = None


class _Function(_Payload):
    name: NonEmpty
    arguments: str


class _WireCall(_Payload):
    id: NonEmpty
    type: Literal["function"]
    function: _Function


class _WireMessage(_Payload):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[_WireCall] | None = None
    tool_call_id: NonEmpty | None = None
    replay: dict[str, JsonValue] | None = Field(
        default=None, alias="_cade_mini_replay_payload"
    )

    @model_validator(mode="after")
    def valid_role_fields(self) -> _WireMessage:
        if self.role == "tool":
            if self.tool_call_id is None or self.content is None:
                raise ValueError("tool messages require content and tool_call_id")
        elif self.tool_call_id is not None:
            raise ValueError("only tool messages can have tool_call_id")
        if self.role != "assistant" and (
            self.tool_calls is not None or self.replay is not None
        ):
            raise ValueError("only assistant messages can have calls or replay state")
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError("message requires content or assistant tool calls")
        return self


class _Tool(_Payload):
    name: NonEmpty
    description: str
    parameters: dict[str, JsonValue]
    builtin: dict[str, JsonValue] | None = None


class _Budget(_Payload):
    context_window: Annotated[int, Field(gt=0)]
    output_reserve: Count
    headroom_tokens: Count

    @model_validator(mode="after")
    def has_input_budget(self) -> _Budget:
        if self.context_window <= self.output_reserve + self.headroom_tokens:
            raise ValueError("budget must leave room for input")
        return self


class _Report(_Payload):
    estimated_tokens: Count
    budget_tokens: Annotated[int, Field(gt=0)]
    over_budget: bool
    dropped_tool_calls: list[str]
    dropped_turns: Count
    context_tokens: Count


class _Snapshot(_Payload):
    messages: list[_WireMessage]
    tools: list[_Tool]
    options: dict[str, JsonValue] | None
    budget: _Budget
    report: _Report

    @model_validator(mode="after")
    def no_runtime_options(self) -> _Snapshot:
        if (
            self.options
            and {"api_key", "headers", "signal", "on_payload", "on_response"}
            & self.options.keys()
        ):
            raise ValueError(
                "snapshot cannot contain runtime or authentication options"
            )
        if self.options is not None:
            if self.options.keys() - _OPTION_NAMES:
                raise ValueError("unknown stream option")
            _OPTIONS.validate_json(json.dumps(self.options), strict=True)
        expected = (
            self.budget.context_window
            - self.budget.output_reserve
            - self.budget.headroom_tokens
        )
        if self.report.budget_tokens != expected:
            raise ValueError("report budget does not match request budget")
        if self.report.over_budget != (self.report.estimated_tokens > expected):
            raise ValueError("report overflow flag does not match token counts")
        return self


class _Request(_Payload):
    model: str
    provider: str
    tools_digest: NonEmpty
    conversation_digest: NonEmpty
    context: list[str]
    estimated_tokens: Count
    dropped: list[str]
    snapshot: _Snapshot

    @model_validator(mode="after")
    def consistent_report(self) -> _Request:
        if self.estimated_tokens != self.snapshot.report.estimated_tokens:
            raise ValueError("request token count does not match snapshot report")
        return self


class _Run(_Payload):
    termination: Literal[
        "completed",
        "max_tokens",
        "step_limit",
        "context_error",
        "provider_error",
        "aborted",
    ]
    steps: Count
    usage: _Usage | None = None
    error: str | None = None


class _Attempt(_Payload):
    model: str
    provider: str
    error: str
    usage: _Usage | None = None


class _Branch(_Payload):
    selected_id: NonEmpty


class _Checkpoint(_Payload):
    checkpoint_id: NonEmpty
    user_id: NonEmpty
    anchor_id: NonEmpty | None
    phase: Literal["started", "completed", "incomplete"]


class _Restore(_Payload):
    operation_id: NonEmpty
    target_id: NonEmpty | None
    draft_id: NonEmpty | None
    scope: Literal["conversation", "code", "both"]
    kind: Literal["undo", "redo", "rewind", "revert"]


_SCHEMAS: dict[EventType, type[_Payload]] = {
    "meta": _Meta,
    "system": _System,
    "user": _User,
    "assistant": _Assistant,
    "tool_result": _ToolResult,
    "compaction": _Compaction,
    "window": _Window,
    "request": _Request,
    "run": _Run,
    "attempt": _Attempt,
    "branch": _Branch,
    "checkpoint": _Checkpoint,
    "restore": _Restore,
}


def validate_event_data(event_type: EventType, data: dict[str, JsonValue]) -> None:
    """验证写入载荷，不补字段，也不修改调用方数据。"""
    schema = _SCHEMAS.get(event_type)
    if schema is None:
        raise ValueError("unknown session event type")
    schema.model_validate(data)
    json.dumps(data, allow_nan=False)
