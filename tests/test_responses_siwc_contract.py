"""保护 SIWC（Sign in with ChatGPT）Responses 请求格式。

覆盖 store/stream 约束、被拒绝的请求字段、additional_tools 输入项，
以及缓存用量解析。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, ClassVar

import pytest
from openai.types.responses import ResponseFailedEvent
from openai.types.responses.input_token_count_response import InputTokenCountResponse

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.providers.responses import (
    ChatGPTResponsesProvider,
    DeepSeekResponsesProvider,
    OpenAIResponsesProvider,
    ProviderRequestError,
    _format_openai_error,
)
from cade.ai.types import ProviderConfig, StreamOptions, ToolDefinition
from cade.harness.loop import run
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "strict": True,
    },
}
_EXPECTED_TEXT_FORMAT = {
    "format": {
        "type": "json_schema",
        "name": "answer",
        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "strict": True,
    }
}
_TOOL = ToolDefinition(
    name="lookup",
    description="Look up a record.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
    },
)
_MESSAGES = [
    {"role": "system", "content": "Follow the response schema."},
    {"role": "developer", "content": "Keep the answer concise."},
    {"role": "user", "content": "Find the first record."},
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_lookup",
                "function": {"name": "lookup", "arguments": '{"query":"first"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_lookup", "content": "Record found."},
    {"role": "user", "content": "Return the result."},
]


class _InputTokensBoundary:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def count(self, **params: Any) -> InputTokenCountResponse:
        self.requests.append(params)
        return InputTokenCountResponse(
            object="response.input_tokens", input_tokens=12345
        )


class _ResponsesBoundary:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.input_tokens = _InputTokensBoundary()

    def create(self, **params: Any) -> list[dict[str, Any]]:
        self.requests.append(params)
        return [
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_fixture",
                    "usage": {
                        "input_tokens": 2711,
                        "input_tokens_details": {
                            "cached_tokens": 2560,
                            "cache_write_tokens": 0,
                        },
                        "output_tokens": 5,
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            }
        ]


class _InjectedClient:
    def __init__(self) -> None:
        self.responses = _ResponsesBoundary()


async def test_input_count_matches_the_inference_payload() -> None:
    client = _InjectedClient()
    provider = OpenAIResponsesProvider(
        ProviderConfig(
            api_key="test-key", model="gpt-5.6-luna", response_format=_RESPONSE_FORMAT
        ),
        client=client,
    )
    options = StreamOptions(
        temperature=0.2, top_p=0.9, max_tokens=128, headers={"X-Fixture": "count"}
    )
    measured = await provider.count_input_tokens(_MESSAGES, [_TOOL], options)
    assert measured == 12345
    assert client.responses.requests == []
    count_request = client.responses.input_tokens.requests[0]
    assert "stream" not in count_request
    assert "store" not in count_request
    assert "max_output_tokens" not in count_request
    events = [event async for event in provider.stream(_MESSAGES, [_TOOL], options)]
    assert any(isinstance(event, UsageUpdate) for event in events)
    inference = client.responses.requests[0]
    for name in (
        "model",
        "input",
        "instructions",
        "tools",
        "text",
        "reasoning",
        "extra_headers",
    ):
        assert count_request.get(name) == inference.get(name), name


@pytest.mark.parametrize(
    ("provider_type", "model"),
    [
        (DeepSeekResponsesProvider, "deepseek-flash"),
        (ChatGPTResponsesProvider, "gpt-5.6-luna"),
    ],
)
async def test_provider_does_not_call_an_undocumented_count_endpoint(
    provider_type: type[OpenAIResponsesProvider], model: str
) -> None:
    client = _InjectedClient()
    provider = provider_type(
        ProviderConfig(api_key="test-key", model=model), client=client
    )
    assert await provider.count_input_tokens(_MESSAGES, [_TOOL]) is None
    assert client.responses.input_tokens.requests == []
    assert client.responses.requests == []


class _FailingResponsesBoundary:
    def create(self, **params: Any) -> list[dict[str, Any]]:
        return [
            {
                "type": "error",
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "The plan usage limit was reached.",
            }
        ]


class _FailingClient:
    def __init__(self) -> None:
        self.responses = _FailingResponsesBoundary()


@pytest.mark.parametrize("event_type", ["response.failed", "response.done"])
async def test_failed_response_is_a_provider_error(
    tmp_path: Path, event_type: str
) -> None:
    # 使用 SDK 的失败事件类型校验故障样例，避免测试依赖不合法的协议数据。
    failed = ResponseFailedEvent.model_validate(
        {
            "type": "response.failed",
            "sequence_number": 2,
            "response": {
                "id": "synthetic_failure",
                "created_at": 0,
                "model": "gpt-5.6-luna",
                "object": "response",
                "output": [],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "status": "failed",
                "error": {"code": "server_error", "message": "Injected failure"},
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_tokens_details": {
                        "cached_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        }
    ).model_dump()
    failed["type"] = event_type

    class FailedResponses(_ResponsesBoundary):
        def create(self, **params: Any) -> list[dict[str, Any]]:
            return [{"type": "response.output_text.delta", "delta": "partial"}, failed]

    client = _InjectedClient()
    client.responses = FailedResponses()
    provider = ChatGPTResponsesProvider(_config(), client=client)
    events = await _collect(provider)
    assert [e for e in events if isinstance(e, UsageUpdate)] == [UsageUpdate(10, 2)]
    failures = [e for e in events if isinstance(e, ProviderFailure)]
    assert len(failures) == 1
    assert failures[0].message == "server_error: Injected failure"
    assert not any(isinstance(e, (FinalMessage, ToolCallEvent)) for e in events)

    result = await run(
        SessionLog.create(tmp_path / "sessions"),
        "test failure",
        provider=provider,
        tools=(),
        budget=Budget(100_000),
        max_provider_retries=0,
    )
    assert result.termination == "provider_error"
    assert result.error == "server_error: Injected failure"
    assert result.usage == {"input": 10, "output": 2}


def _config(
    model: str = "gpt-6-luna",
    *,
    thinking: bool = False,
    response_format: dict[str, Any] | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        api_key="",
        model=model,
        base_url="https://api.openai.com/v1",
        thinking=thinking,
        response_format=response_format,
    )


async def _collect(
    provider: ChatGPTResponsesProvider | OpenAIResponsesProvider,
    *,
    messages: list[dict[str, Any]] | None = None,
    tools: list[ToolDefinition] | None = None,
    options: StreamOptions | None = None,
) -> list[ProviderEvent]:
    return [
        event
        async for event in provider.stream(
            messages if messages is not None else [{"role": "user", "content": "Hi."}],
            tools if tools is not None else [],
            options,
        )
    ]


@pytest.mark.asyncio
async def test_siwc_request_shape_and_usage() -> None:
    client = _InjectedClient()
    provider = ChatGPTResponsesProvider(
        _config(response_format=_RESPONSE_FORMAT), client=client
    )
    events = await _collect(
        provider,
        messages=_MESSAGES,
        tools=[_TOOL],
        options=StreamOptions(
            temperature=0.2,
            top_p=0.9,
            max_tokens=1024,
            session_id="explicit-session",
            headers={"x-trace-label": "preserved"},
        ),
    )

    request = client.responses.requests[0]
    assert request["store"] is False
    assert request["stream"] is True
    assert request["instructions"] == (
        "Follow the response schema.\n\nKeep the answer concise."
    )
    assert "tools" not in request
    for unsupported in (
        "max_output_tokens",
        "stream_options",
        "temperature",
        "top_p",
    ):
        assert unsupported not in request
    assert request["prompt_cache_key"] == "explicit-session"
    assert request["extra_headers"] == {
        "x-trace-label": "preserved",
        "session-id": "explicit-session",
        "thread-id": "explicit-session",
    }
    assert request["text"] == _EXPECTED_TEXT_FORMAT
    assert request["input"] == [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [
                {
                    "type": "function",
                    "name": "lookup",
                    "description": "Look up a record.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    "strict": False,
                }
            ],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Find the first record."}],
        },
        {
            "type": "function_call",
            "call_id": "call_lookup",
            "name": "lookup",
            "arguments": '{"query":"first"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_lookup",
            "output": "Record found.",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Return the result."}],
        },
    ]

    assert [event for event in events if isinstance(event, UsageUpdate)] == [
        UsageUpdate(input_tokens=2711, output_tokens=5)
    ]
    assert provider.usage_totals.input_tokens == 151
    assert provider.usage_totals.cache_read_tokens == 2560
    assert provider.usage_totals.output_tokens == 5
    assert provider.metrics["cached_tokens"] == 2560
    assert provider.cache_hit_rate == 0.9443


@pytest.mark.asyncio
async def test_siwc_without_tools_keeps_plain_input() -> None:
    client = _InjectedClient()
    provider = ChatGPTResponsesProvider(_config(), client=client)
    await _collect(provider)

    request = client.responses.requests[0]
    assert request["store"] is False
    assert request["stream"] is True
    assert "tools" not in request
    assert request["input"] == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hi."}],
        }
    ]


async def test_siwc_cache_affinity_is_stable_and_provider_local() -> None:
    client = _InjectedClient()
    first = ChatGPTResponsesProvider(_config(), client=client)
    await _collect(first)
    await _collect(first, tools=[_TOOL])
    second = ChatGPTResponsesProvider(_config(), client=client)
    await _collect(second)

    keys = [request["prompt_cache_key"] for request in client.responses.requests]
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]
    assert str(uuid.UUID(keys[0])) == keys[0]
    for request in client.responses.requests:
        assert request["extra_headers"] == {
            "session-id": request["prompt_cache_key"],
            "thread-id": request["prompt_cache_key"],
        }


@pytest.mark.parametrize("session_id", ["chosen-session", "long-session-" * 20])
async def test_siwc_cache_override_matches_headers_and_restores_default(
    session_id: str,
) -> None:
    client = _InjectedClient()
    provider = ChatGPTResponsesProvider(_config(), client=client)
    await _collect(provider)
    await _collect(
        provider,
        options=StreamOptions(
            session_id=session_id,
            headers={"SESSION-ID": "wrong", "Thread-Id": "wrong", "x-trace": "keep"},
        ),
    )
    await _collect(provider)

    initial, overridden, restored = client.responses.requests
    expected = (
        hashlib.sha256(session_id.encode()).hexdigest()
        if len(session_id) > 64
        else session_id
    )
    assert overridden["prompt_cache_key"] == expected
    assert len(expected) <= 64
    assert overridden["extra_headers"] == {
        "session-id": expected,
        "thread-id": expected,
        "x-trace": "keep",
    }
    assert initial["prompt_cache_key"] == restored["prompt_cache_key"]
    assert initial["prompt_cache_key"] != expected


@pytest.mark.asyncio
async def test_siwc_stream_error_surfaces_code_and_message() -> None:
    provider = ChatGPTResponsesProvider(_config(), client=_FailingClient())

    with pytest.raises(
        ProviderRequestError,
        match="subscription_sharing_usage_limit_exceeded: The plan usage limit",
    ):
        await _collect(provider)


def test_sdk_error_without_status_keeps_structured_detail() -> None:
    class _UsageLimitError(Exception):
        status_code = None
        body: ClassVar[dict[str, str]] = {
            "type": "invalid_request_error",
            "code": "subscription_sharing_usage_limit_exceeded",
            "message": "The ChatGPT user has reached their usage limit.",
        }

    formatted = _format_openai_error(_UsageLimitError())

    assert "subscription_sharing_usage_limit_exceeded" in str(formatted)
    assert "usage limit" in str(formatted)


@pytest.mark.asyncio
async def test_regular_openai_responses_request_remains_unchanged() -> None:
    client = _InjectedClient()
    provider = OpenAIResponsesProvider(
        _config("gpt-5.5", response_format=_RESPONSE_FORMAT), client=client
    )
    await _collect(
        provider,
        messages=_MESSAGES,
        tools=[_TOOL],
        options=StreamOptions(session_id="chatgpt-session"),
    )

    request = client.responses.requests[0]
    assert request["instructions"] == (
        "Follow the response schema.\n\nKeep the answer concise."
    )
    assert request["tools"][0]["name"] == "lookup"
    assert request["text"] == _EXPECTED_TEXT_FORMAT
    assert "parallel_tool_calls" not in request
    assert "prompt_cache_key" not in request
    assert "store" not in request
    assert "extra_headers" not in request
