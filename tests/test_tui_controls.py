"""终端取消通知、会话过滤和模型发现的回归测试。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from cade.ai.auth import AuthCredential
from cade.ai.events import ProviderEvent, ReasoningDelta, TextDelta
from cade.ai.models import get_models
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.auth import JsonCredentialStore
from cade.app.settings import SettingsService
from cade.harness.cancellation import CancellationToken
from cade.harness.loop import run
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog, list_sessions
from cade.hosts.tui.app import installed_version
from cade.hosts.tui.command_handlers import COMMAND_NAMES, COMMAND_REGISTRY
from cade.hosts.tui.completion import CompletionEngine


def test_welcome_version_reads_installed_distribution() -> None:
    """欢迎区版本必须来自当前安装的分发包，避免改名后读取残留元数据。"""
    assert installed_version() == version("cade")


def test_terminal_setup_does_not_replace_sigint_handler() -> None:
    script = (
        "import signal; "
        "from cade.hosts.tui.ptk_patch import suppress_windows_ptk_shutdown_noise; "
        "before = signal.getsignal(signal.SIGINT); "
        "suppress_windows_ptk_shutdown_noise(); "
        "assert signal.getsignal(signal.SIGINT) is before"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class WaitingProvider:
    model = "gpt-5.5"
    transport = "openai_responses"

    def __init__(self, partial: bool, reasoning: bool = False) -> None:
        self.partial = partial
        self.reasoning = reasoning
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        try:
            if self.reasoning:
                yield ReasoningDelta("reasoning summary")
            if self.partial:
                yield TextDelta("partial answer")
            self.started.set()
            await asyncio.sleep(60)
        finally:
            self.closed.set()


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("reasoning", [False, True])
async def test_cancel_wakes_waiting_provider_and_closes_stream(
    tmp_path: Path, partial: bool, reasoning: bool
) -> None:
    log = SessionLog.create(tmp_path / "sessions")
    provider = WaitingProvider(partial, reasoning)
    token = CancellationToken()
    task = asyncio.create_task(
        run(
            log,
            "waiting task",
            provider=provider,
            tools=(),
            budget=Budget(100_000),
            cancel=token,
        )
    )
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        await asyncio.to_thread(token.cancel, "keyboard interrupt")
        result = await asyncio.wait_for(task, timeout=0.5)
        assert result.termination == "aborted"
        assert result.error == "keyboard interrupt"
        assert provider.closed.is_set()
        assert result.answer == ("partial answer" if partial else "")
        assistants = [entry for entry in log.branch() if entry.type == "assistant"]
        assert len(assistants) == int(partial or reasoning)
        if reasoning:
            reopened = SessionLog.open(log.path)
            assistant = next(
                entry for entry in reopened.branch() if entry.type == "assistant"
            )
            assert assistant.data["reasoning"] == "reasoning summary"
            assert assistant.data["response_metrics"]["tokens_per_second"] is None
        assert log.branch()[-1].data["termination"] == "aborted"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_caller_task_cancellation_is_not_a_provider_failure(
    tmp_path: Path,
) -> None:
    provider = WaitingProvider(False)
    task = asyncio.create_task(
        run(
            SessionLog.create(tmp_path / "sessions"),
            "waiting task",
            provider=provider,
            tools=(),
            budget=Budget(100_000),
            cancel=CancellationToken(),
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed.is_set()


def test_resumable_sessions_exclude_empty_and_runtime_only(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    untouched = SessionLog.create(sessions, session_id="untouched")
    untouched.append("system", {"text": "initialization"})
    blank = SessionLog.create(sessions, session_id="blank")
    blank.append("user", {"text": " \n\t"})
    runtime = SessionLog.create(sessions, session_id="runtime")
    runtime.append("user", {"text": "internal guidance", "source": "runtime"})
    usable = SessionLog.create(sessions, session_id="usable")
    usable.append("user", {"text": "real task"})

    assert [info.id for info in list_sessions(sessions)] == ["usable"]
    assert len(list(sessions.glob("session-*.jsonl"))) == 4


@pytest.mark.parametrize(
    "expires,refresh,expected",
    [
        (1, "refresh-token", ("chatgpt",)),
        (1, None, ()),
        (9_999_999_999, None, ("chatgpt",)),
    ],
)
def test_chatgpt_model_discovery_accepts_refreshable_login(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expires: int,
    refresh: str | None,
    expected: tuple[str, ...],
) -> None:
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    store = JsonCredentialStore(tmp_path / "auth.json")
    store.save(
        AuthCredential(
            provider="chatgpt",
            type="oauth",
            access="test-token",
            refresh=refresh,
            expires=expires,
        )
    )
    assert SettingsService(tmp_path).configured_providers(store) == expected
    assert store.get("chatgpt").access == "test-token"
    assert [model.id for model in get_models("chatgpt")] == [
        model.id for model in get_models("openai")
    ]
    assert get_models("chatgpt")


@pytest.mark.parametrize(
    "command,canonical",
    [
        ("/clear", "/new"),
        ("/continue", "/sessions"),
        ("/quit", "/exit"),
    ],
)
def test_alias_completion_keeps_description_and_arguments(
    tmp_path: Path, command: str, canonical: str
) -> None:
    engine = CompletionEngine(
        tmp_path,
        command_names=COMMAND_NAMES,
        command_registry=COMMAND_REGISTRY,
    )
    match = next(item for item in engine.complete(command) if item.text == command)
    assert match.display_meta
    assert COMMAND_REGISTRY[command].canonical == canonical
    if COMMAND_REGISTRY[command].args_desc:
        assert engine.command_args[command] == COMMAND_REGISTRY[command].args_desc
