"""验证运行内连接复用、客户端归属及跨事件循环清理。"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from openai.types.responses.input_token_count_response import InputTokenCountResponse

from cade.ai.providers.base import SessionProvider
from cade.ai.providers.responses import (
    ChatGPTResponsesProvider,
    DeepSeekResponsesProvider,
    OpenAIResponsesProvider,
)
from cade.ai.types import ProviderConfig
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.harness.loop import run
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog

_MESSAGES = [{"role": "user", "content": "Reply DONE."}]
_CONFIG = ProviderConfig(
    api_key="test-key", model="gpt-5.6-luna", reasoning_effort="high"
)


class _Responses:
    def __init__(
        self,
        tag: str,
        *,
        tool_round: bool,
        failure: Exception | None,
        started: asyncio.Event | None,
    ) -> None:
        self.tag = tag
        self.tool_round = tool_round
        self.failure = failure
        self.started = started
        self.requests: list[dict[str, Any]] = []
        self.input_tokens = self

    async def count(self, **params: Any) -> InputTokenCountResponse:
        return InputTokenCountResponse(object="response.input_tokens", input_tokens=7)

    async def create(self, **params: Any) -> list[dict[str, Any]]:
        self.requests.append(params)
        if self.failure is not None:
            raise self.failure
        if self.started is not None:
            self.started.set()
            await asyncio.Event().wait()
        if self.tool_round and len(self.requests) == 1:
            output = {
                "type": "function_call",
                "id": f"fc_{self.tag}",
                "call_id": f"call_{self.tag}",
                "name": "read",
                "arguments": '{"path":"sample.txt"}',
                "status": "completed",
            }
            events: list[dict[str, Any]] = []
        else:
            output = {
                "type": "message",
                "id": f"msg_{self.tag}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "DONE", "annotations": []}],
            }
            events = [{"type": "response.output_text.delta", "delta": "DONE"}]
        return [
            *events,
            {
                "type": "response.completed",
                "response": {
                    "id": f"resp_{self.tag}_{len(self.requests)}",
                    "status": "completed",
                    "output": [output],
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                },
            },
        ]


class _Client:
    def __init__(self, responses: _Responses) -> None:
        self.responses = responses
        self.loop = asyncio.get_running_loop()
        self.close_calls = 0

    async def close(self) -> None:
        assert asyncio.get_running_loop() is self.loop
        self.close_calls += 1


def _clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool_round: bool = False,
    failure: Exception | None = None,
    started: asyncio.Event | None = None,
) -> list[_Client]:
    clients: list[_Client] = []

    def build(**kwargs: Any) -> _Client:
        client = _Client(
            _Responses(
                str(len(clients)),
                tool_round=tool_round,
                failure=failure,
                started=started,
            )
        )
        clients.append(client)
        return client

    monkeypatch.setattr("openai.AsyncOpenAI", build)
    return clients


async def _collect(provider: OpenAIResponsesProvider) -> None:
    async for _event in provider.stream(_MESSAGES, []):
        pass


@pytest.mark.parametrize(
    "provider_type",
    [OpenAIResponsesProvider, ChatGPTResponsesProvider, DeepSeekResponsesProvider],
)
async def test_session_reuses_and_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch, provider_type: type[OpenAIResponsesProvider]
) -> None:
    clients = _clients(monkeypatch)
    provider = provider_type(_CONFIG)
    assert isinstance(provider, SessionProvider)
    async with provider.session():
        await _collect(provider)
        async with provider.session():
            await _collect(provider)
        assert len(clients) == 1
        assert clients[0].close_calls == 0
    assert len(clients[0].responses.requests) == 2
    assert clients[0].close_calls == 1
    async with provider.session():
        await _collect(provider)
    assert len(clients) == 2
    assert clients[1].close_calls == 1


async def test_session_does_not_close_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _clients(monkeypatch)
    client = _Client(
        _Responses("injected", tool_round=False, failure=None, started=None)
    )
    provider = ChatGPTResponsesProvider(_CONFIG, client=client)
    async with provider.session():
        await _collect(provider)
    await _collect(provider)
    assert clients == []
    assert client.close_calls == 0
    await client.close()


async def test_standalone_streams_keep_per_call_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _clients(monkeypatch)
    provider = ChatGPTResponsesProvider(_CONFIG)
    await _collect(provider)
    await _collect(provider)
    assert [client.close_calls for client in clients] == [1, 1]


async def test_count_and_stream_share_session_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _clients(monkeypatch)
    provider = OpenAIResponsesProvider(_CONFIG)
    async with provider.session():
        assert await provider.count_input_tokens(_MESSAGES, []) == 7
        await _collect(provider)
        assert len(clients) == 1
        assert clients[0].close_calls == 0
    assert clients[0].close_calls == 1


@pytest.mark.parametrize("outcome", ["failure", "cancelled"])
async def test_run_closes_client_on_failure_or_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    started = asyncio.Event() if outcome == "cancelled" else None
    clients = _clients(
        monkeypatch,
        failure=RuntimeError("injected failure") if outcome == "failure" else None,
        started=started,
    )
    provider = ChatGPTResponsesProvider(_CONFIG)
    task = asyncio.create_task(
        run(
            SessionLog.create(tmp_path),
            "reply",
            provider=provider,
            tools=(),
            budget=Budget(100_000),
            max_provider_retries=0,
        )
    )
    if started is not None:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert (await task).termination == "provider_error"
    assert len(clients) == 1
    assert clients[0].close_calls == 1


def test_application_reuses_within_turn_and_cleans_before_loop_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients = _clients(monkeypatch, tool_round=True)
    provider = ChatGPTResponsesProvider(
        ProviderConfig(
            api_key="test-key",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            context_window=100_000,
        )
    )
    (tmp_path / "sample.txt").write_text("sample", encoding="utf-8")
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
        ).session
        for _ in range(2):
            result = asyncio.run(
                session.run_task("inspect sample.txt", lambda event: None)
            )
            assert result.termination == "completed"
            assert result.answer == "DONE"
            assert clients[-1].close_calls == 1
            assert clients[-1].loop.is_closed()
    assert len(clients) == 2
    assert all(len(client.responses.requests) == 2 for client in clients)
