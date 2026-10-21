"""DeepSeek Responses provider 的解析、请求与流式回归测试。"""

from __future__ import annotations

from typing import Any

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ReasoningDelta,
    TextDelta,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.models import get_model
from cade.ai.providers.responses import DeepSeekResponsesProvider
from cade.ai.resolver import ModelResolver
from cade.ai.types import ProviderConfig, ToolDefinition


class _ResponsesBoundary:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._events = events or []

    def create(self, **params: Any) -> list[dict[str, Any]]:
        self.requests.append(params)
        return self._events


class _InjectedClient:
    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.responses = _ResponsesBoundary(events)


def _config(*, thinking: bool = False) -> ProviderConfig:
    return ProviderConfig(
        api_key="deepseek-test-key",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        thinking=thinking,
        reasoning_effort="high",
    )


async def _collect(
    provider: DeepSeekResponsesProvider,
    messages: list[dict[str, Any]],
    tools: list[ToolDefinition] | None = None,
) -> list[ProviderEvent]:
    return [event async for event in provider.stream(messages, tools or [], None)]


def test_resolver_selects_deepseek_transport() -> None:
    resolution = ModelResolver.resolve("deepseek-flash")

    assert resolution.provider == "deepseek"
    assert resolution.transport == "deepseek_responses"
    assert resolution.default_base_url == "https://api.deepseek.com"


def test_resolver_accepts_explicit_deepseek_provider() -> None:
    resolution = ModelResolver.resolve("deepseek/deepseek-v4-pro")

    assert resolution.model == "deepseek-v4-pro"
    assert resolution.provider == "deepseek"
    assert resolution.transport == "deepseek_responses"


def test_deepseek_base_url_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://proxy.example/v1")

    resolution = ModelResolver.resolve("deepseek-flash")

    assert resolution.default_base_url == "https://proxy.example/v1"


def test_deepseek_model_metadata() -> None:
    flash = get_model("deepseek", "deepseek-flash")

    assert flash is not None
    assert flash.context_window == 1_000_000
    assert flash.max_tokens == 384_000
    assert flash.reasoning_efforts == ("none", "low", "high", "max")


@pytest.mark.asyncio
async def test_deepseek_request_omits_openai_only_fields() -> None:
    client = _InjectedClient()
    provider = DeepSeekResponsesProvider(_config(), client=client)
    tool = ToolDefinition(
        name="lookup",
        description="Look up a record.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )

    await _collect(
        provider,
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Find the first record."},
        ],
        [tool],
    )

    request = client.responses.requests[0]
    assert request["model"] == "deepseek-flash"
    assert request["stream"] is True
    assert request["instructions"] == "Be concise."
    assert request["reasoning"] == {"effort": "high"}
    assert "stream_options" not in request
    assert "store" not in request
    assert request["tools"][0]["strict"] is False
    assert "required" not in request["tools"][0]["parameters"]
    assert provider.transport == "deepseek_responses"
    assert provider.metrics["transport"] == "deepseek_responses"


@pytest.mark.asyncio
async def test_deepseek_stream_normalizes_official_events() -> None:
    events = [
        {
            "type": "response.reasoning_text.delta",
            "delta": "plan",
            "output_index": 0,
            "item_id": "rs_1",
        },
        {"type": "response.output_text.delta", "delta": "Checking."},
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "call_id": "call_1",
            "delta": '{"query"',
        },
        {
            "type": "response.function_call_arguments.delta",
            "call_id": "call_1",
            "delta": ':"first"}',
        },
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": '{"query":"first"}',
                "status": "completed",
            },
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "status": "completed",
                        "content": [{"type": "reasoning_text", "text": "plan"}],
                    },
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Checking."}],
                    },
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": '{"query":"first"}',
                        "status": "completed",
                    },
                ],
                "usage": {
                    "input_tokens": 30,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 4},
                },
            },
        },
    ]
    client = _InjectedClient(events)
    provider = DeepSeekResponsesProvider(_config(), client=client)

    result = await _collect(provider, [{"role": "user", "content": "Find it."}])

    assert [event for event in result if isinstance(event, ReasoningDelta)] == [
        ReasoningDelta(chunk="plan")
    ]
    assert [event for event in result if isinstance(event, TextDelta)] == [
        TextDelta(chunk="Checking.")
    ]
    calls = [event for event in result if isinstance(event, ToolCallEvent)]
    assert calls[0].calls[0].name == "lookup"
    assert calls[0].calls[0].input == {"query": "first"}
    final = [event for event in result if isinstance(event, FinalMessage)]
    assert final[0].stop_reason == "tool_use"
    assert final[0].content == "Checking."
    assert [event for event in result if isinstance(event, UsageUpdate)] == [
        UsageUpdate(input_tokens=30, output_tokens=10)
    ]
    assert provider.usage_totals.output_tokens == 10
