"""通过真实输入事件验证 TUI 命令、会话和本地持久化。"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.output import DummyOutput

from cade.ai.auth import AuthCredential
from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app import auth
from cade.app.commands import SPECS
from cade.app.session import ApplicationSession
from cade.harness.cancellation import CancellationToken
from cade.harness.session.log import SessionLog
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult
from cade.hosts import cli
from cade.hosts.tui import app as tui_module
from cade.hosts.tui.bridge import CadeInteractionApp
from cade.hosts.tui.config_registry import SETTING_SPECS

pytestmark = pytest.mark.e2e


class ScriptedProvider:
    """只替换模型边界；命令、核心循环、工具和存储使用真实组件。"""

    model = "gpt-5.5"
    transport = "openai_responses"
    context_window = 1_050_000

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []
        self.delay = 0.0
        self.batches: list[list[ProviderEvent]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        batch = (
            self.batches.pop(0)
            if self.batches
            else [TextDelta("E2E answer"), FinalMessage("E2E answer", "end_turn")]
        )
        for event in batch:
            yield event


class RecordingOutput(DummyOutput):
    def __init__(self) -> None:
        self.fragments: list[str] = []

    def write(self, data: str) -> None:
        self.fragments.append(data)

    def write_raw(self, data: str) -> None:
        self.fragments.append(data)


class Terminal:
    def __init__(
        self, tui: tui_module._CadeTui, pipe: PipeInput, service: ApplicationSession
    ) -> None:
        self.tui, self.pipe, self.service = tui, pipe, service

    @property
    def transcript(self) -> str:
        return "\n".join(entry.text for entry in self.tui._state.log)

    async def until(self, predicate: Callable[[], bool], label: str) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)
        assert predicate(), label

    async def send(self, text: str, *, wait_idle: bool = True) -> None:
        self.pipe.send_text("\x15" + text)
        await self.until(lambda: self.tui._input.text == text, f"input {text}")
        # 补全开启时，先用左箭头关闭补全，再提交原始输入。
        self.pipe.send_text("\x1b[D\r")
        await asyncio.sleep(0.08)
        if wait_idle:
            await self.until(lambda: not self.tui._state.running, f"complete {text}")

    async def choose(self, index: int = 0) -> None:
        assert self.tui._state.pending_command_choice is not None
        offset = index - self.tui._command_choices._selected_index
        arrow = "\x1b[B" if offset >= 0 else "\x1b[A"
        self.pipe.send_text(arrow * abs(offset) + "\r")
        await asyncio.sleep(0.08)
        await self.until(lambda: not self.tui._state.running, "picker completion")

    async def escape(self) -> None:
        self.pipe.send_text("\x1b")
        await asyncio.sleep(0.6)


Scenario = Callable[[Terminal], Awaitable[None]]


class Runner(Protocol):
    def __call__(
        self,
        scenario: Scenario,
        *,
        resume: str | None = None,
        initial_prompt: str | None = None,
    ) -> None: ...


@pytest.fixture
def terminal_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runner:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifacts = (
        Path(__file__).resolve().parents[1] / "e2e-results" / "tui" / tmp_path.name
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    (workspace / "cade.json").write_text(
        '{"trust_project_skills": true}', encoding="utf-8"
    )
    skill = workspace / ".cade" / "skills" / "e2e-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: e2e-skill\ndescription: E2E fixture\n---\nE2E skill body\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(auth, "DEFAULT_AUTH_FILE", home / "auth.json")
    monkeypatch.setattr(auth, "DEFAULT_HOST_FILE", home / "host.json")
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "CHATGPT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    provider = ScriptedProvider()
    # 模型切换也只替换外部边界，不依赖维护者的真实凭据。
    monkeypatch.setattr("cade.app.session.build_provider", lambda _: provider)

    def run(
        scenario: Scenario,
        *,
        resume: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        def launch(service: ApplicationSession, root: Path, **kwargs: Any) -> int:
            assert [s.name for s in service.skills] == ["e2e-skill"]
            with create_pipe_input() as pipe:
                output = RecordingOutput()
                tui = tui_module._CadeTui(
                    CadeInteractionApp(service),
                    root,
                    input=pipe,
                    output=output,
                    **kwargs,
                )
                terminal = Terminal(tui, pipe, service)
                errors: list[BaseException] = []
                initial_threads = set(threading.enumerate())

                async def drive() -> None:
                    try:
                        await asyncio.sleep(0.05)
                        await scenario(terminal)
                    except (AssertionError, OSError, RuntimeError, ValueError) as exc:
                        errors.append(exc)
                    finally:
                        if service.cancel is not None:
                            service.cancel.cancel("test cleanup")
                        workers = [
                            thread
                            for thread in threading.enumerate()
                            if thread not in initial_threads
                            and "_run_turn" in thread.name
                        ]
                        for worker in workers:
                            await asyncio.to_thread(worker.join, 2)
                            if worker.is_alive():
                                errors.append(AssertionError("TUI worker did not stop"))
                        await asyncio.sleep(0)
                        (artifacts / "transcript.txt").write_text(
                            terminal.transcript, encoding="utf-8"
                        )
                        (artifacts / "rendered.txt").write_text(
                            "".join(output.fragments), encoding="utf-8"
                        )
                        if tui._application.is_running:
                            tui._application.exit()

                async def execute() -> None:
                    driver = asyncio.create_task(drive())
                    await tui._application.run_async()
                    await driver

                asyncio.run(execute())
                if errors:
                    raise errors[0]
            return 0

        monkeypatch.setattr(tui_module, "run_tui", launch)
        assert (
            cli.main(
                [
                    *([initial_prompt] if initial_prompt is not None else []),
                    "--project-root",
                    str(workspace),
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.5",
                    "--yes",
                    *(["--resume", resume] if resume is not None else []),
                ],
                provider=provider,
            )
            == 0
        )

    return run


@pytest.mark.parametrize("resume", [False, True])
def test_startup_prompt_is_sent_once_and_tui_stays_open(
    terminal_runner: Runner, resume: bool
) -> None:
    previous_session: list[str] = []

    async def prepare(term: Terminal) -> None:
        await term.send("earlier message")
        previous_session.append(term.service.log.session_id)

    if resume:
        terminal_runner(prepare)

    async def scenario(term: Terminal) -> None:
        await term.until(
            lambda: not term.tui._state.running and "E2E answer" in term.transcript,
            "initial prompt completed",
        )
        users = [e.data["text"] for e in term.service.log.branch() if e.type == "user"]
        assert users == (["earlier message"] if resume else []) + ["首次任务"]
        assert term.tui._application.is_running
        term.tui._application.invalidate()
        await term.send("follow-up message")
        users = [e.data["text"] for e in term.service.log.branch() if e.type == "user"]
        assert users.count("首次任务") == 1
        assert users[-1] == "follow-up message"
        if resume:
            assert term.service.log.session_id == previous_session[0]

    terminal_runner(
        scenario,
        initial_prompt="首次任务",
        resume=previous_session[0] if resume else None,
    )


# 每个目录项都有独立用例；新增命令时要求明确补充可观察结果。
COMMAND_CASES = [
    ("/help", "Help"),
    ("/new", None),
    ("/clear", None),
    ("/continue", "picker"),
    ("/resume", "picker"),
    ("/sessions", "picker"),
    ("/fork 1", None),
    ("/clone", None),
    ("/undo", None),
    ("/redo", "Nothing to redo"),
    ("/rewind", "picker"),
    ("/revert", "picker"),
    ("/tree", "picker"),
    ("/compact", "Nothing to compact"),
    ("/rollover --force", "Context reset"),
    ("/new-context --force", "Context reset"),
    ("/model openai/gpt-5.5", "Model:"),
    ("/effort low", "Model:"),
    ("/thinking off", "Thinking level: off"),
    ("/verbose auto", "Display verbosity: auto"),
    ("/context-window 200k", "200,000"),
    ("/context", "GPT-5.5"),
    ("/mode ask", "Mode: ask"),
    ("/queue followup", "Busy input mode: followup"),
    ("/steer guidance", None),
    ("/stop", "No running task"),
    ("/btw independent question", "E2E answer"),
    ("/goal E2E objective", "active: E2E objective"),
    ("/login openai api_key", "text"),
    ("/connect deepseek api_key", "text"),
    ("/logout openai", "No openai credentials"),
    ("/auth status", "No stored provider credentials"),
    ("/config", "picker"),
    ("/permissions list", "No grants"),
    ("/tool list", "read"),
    ("/skill e2e-skill", None),
    ("/memory", "MEMORY.md"),
    ("/exit", "exit"),
    ("/quit", "exit"),
]


def test_command_inventory_is_complete() -> None:
    assert {line.split()[0] for line, _ in COMMAND_CASES} == {
        spec.name for spec in SPECS
    }


def test_context_shows_compact_categories_and_loaded_skill_files(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("hello, i guess?")
        original = term.service.log.path.read_bytes()
        await term.send("/context")
        report = term.tui._state.log[-1].text
        assert "Context · GPT-5.5" in report
        assert "Estimated input:" in report
        assert "Input sources (estimated" in report
        assert "User messages:" in report
        assert "Built-in tools:" in report
        assert "Input remaining:" in report
        assert "Skill catalog (1 available)" in report
        assert "hello, i guess?" not in report
        assert "E2E answer" not in report
        assert "Tool definitions" not in report
        assert "Skill files" not in report
        assert "□" not in report
        assert "Subagents:" not in report
        assert len(report.splitlines()) <= 18
        assert term.service.log.path.read_bytes() == original
        await term.send("$e2e-skill check this")
        await term.send("/context")
        report = term.tui._state.log[-1].text
        assert "Skill files" in report
        assert "SKILL.md:" in report
        assert "E2E skill body" not in report
        assert len(term.service.provider.requests) == 2

    terminal_runner(scenario)


def test_context_displays_remote_input_count_without_another_model_run(
    terminal_runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[dict[str, Any]]] = []

    async def count(
        self: ScriptedProvider,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> int:
        calls.append(messages)
        return 4321

    monkeypatch.setattr(ScriptedProvider, "count_input_tokens", count, raising=False)

    async def scenario(term: Terminal) -> None:
        await term.send("first task")
        before = term.service.log.path.read_bytes()
        await term.send("/context")
        report = term.tui._state.log[-1].text
        assert "Input (provider count): 4,321 / 1,050,000" in report
        assert "Input sources (estimated" in report
        assert len(calls) == 1
        assert len(term.service.provider.requests) == 1
        assert term.service.log.path.read_bytes() == before

    terminal_runner(scenario)


@pytest.mark.parametrize(
    "command,expected",
    COMMAND_CASES,
    ids=[line.split()[0] for line, _ in COMMAND_CASES],
)
def test_each_slash_command(
    terminal_runner: Runner, command: str, expected: str | None
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("first task")
        original = term.service.log.session_id
        original_path = term.service.log.path
        before = len(term.tui._state.log)
        if command.startswith("/model "):
            term.service.auth.save_api_key("openai", "e2e-model-key")
        await term.send(command)
        name = command.split()[0]
        if expected == "picker":
            assert term.tui._state.pending_command_choice is not None
        elif expected == "text":
            assert term.tui._state.pending_command_text is not None
        elif expected == "exit":
            assert not term.tui._application.is_running
        elif expected is not None:
            visible = "\n".join(e.text for e in term.tui._state.log[before:])
            assert expected in visible
        if name == "/btw":
            assert len(term.service.provider.requests) == 2
            users = [
                e.data["text"] for e in term.service.log.branch() if e.type == "user"
            ]
            assert users == ["first task"]
        if name in {"/new", "/clear", "/clone", "/fork"}:
            assert term.service.log.session_id != original
            assert SessionLog.open(original_path).session_id == original
            users = [e for e in term.service.log.branch() if e.type == "user"]
            assert len(users) == (1 if name == "/clone" else 0)
        if name == "/fork":
            assert term.tui._input.text == "first task"
        if name == "/steer":
            assert term.tui._input.text == "guidance"
        if name == "/skill":
            await term.send("use activated skill")
            assert "E2E skill body" in json.dumps(term.service.provider.requests[-1])
        if name == "/mode":
            assert term.service.policy.mode == "ask"
            assert term.tui._state.mode == "ask"
        if name == "/thinking":
            assert term.service.provider_config.reasoning_effort == "none"
            assert term.service.provider_config.thinking is True
        if name == "/effort":
            assert term.service.provider_config.reasoning_effort == "low"
        if name == "/context-window":
            assert term.service.budget.context_window == 200_000
        if name in {"/rollover", "/new-context"}:
            assert any(
                e.type == "window" for e in SessionLog.open(original_path).branch()
            )
        if name == "/goal":
            assert term.service.goals.load().objective == "E2E objective"

    terminal_runner(scenario)


def test_session_pickers_and_resume(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("original task")
        original = term.service.log.session_id
        await term.send("/new")
        await term.send("second task")
        second = term.service.log.session_id
        await term.send(f"/resume {original}")
        assert term.service.log.session_id == original
        assert "original task" in term.transcript
        assert "second task" not in term.transcript
        await term.send("/sessions")
        await term.choose(0)
        assert term.service.log.session_id == second
        await term.send("/resume")
        await term.choose(1)
        assert term.service.log.session_id == original

    terminal_runner(scenario)


def test_native_mode_and_permissions(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/mode")
        await term.choose(2)
        assert term.service.policy.mode == "plan"
        term.service.policy.grants.add(
            ToolCallBlock(id="grant-1", name="read", args={"path": "allowed.txt"})
        )
        assert term.service.policy.grants.describe()
        await term.send("/permissions")
        await term.choose(1)
        assert not term.service.policy.grants.describe()
        assert "Permissions cleared" in term.transcript

    terminal_runner(scenario)


@pytest.mark.parametrize(
    "key,initial",
    [
        ("read_only", False),
        ("instructions", True),
        ("note", True),
        ("memory", True),
        ("auto_review", False),
        ("network", True),
    ],
)
def test_config_boolean_picker(
    terminal_runner: Runner, key: str, initial: bool
) -> None:
    async def scenario(term: Terminal) -> None:
        path = term.service.workspace / "cade.json"
        original = path.read_bytes()
        await term.send(f"/config {key}")
        assert term.tui._state.pending_command_text is None
        assert term.tui._command_choices._selected_index == (0 if initial else 1)
        await term.escape()
        assert path.read_bytes() == original
        await term.escape()
        await term.send(f"/config {key}")
        await term.choose(1 if initial else 0)
        assert json.loads(path.read_text(encoding="utf-8"))[key] is not initial
        assert term.tui._state.pending_command_choice is not None
        await term.escape()
        await term.send(f"/config {key}")
        assert term.tui._command_choices._selected_index == (1 if initial else 0)

    terminal_runner(scenario)


def test_config_persists_and_rejects_invalid_input(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/config max_steps")
        await term.choose(len(term.tui._command_choices.values) - 1)
        await term.send("7")
        path = term.service.workspace / "cade.json"
        assert json.loads(path.read_text(encoding="utf-8"))["max_steps"] == 7
        await term.escape()
        await term.send("/config max_steps")
        await term.choose(len(term.tui._command_choices.values) - 1)
        assert term.tui._input.text == "7"
        await term.escape()
        assert term.tui._state.pending_command_text is None
        assert term.tui._state.pending_command_choice is not None
        await term.choose(len(term.tui._command_choices.values) - 1)
        await term.send("not-a-number")
        assert json.loads(path.read_text(encoding="utf-8"))["max_steps"] == 7

    terminal_runner(scenario)


def test_config_always_offers_choices_before_custom_input(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        for spec in SETTING_SPECS:
            await term.send(f"/config {spec.key}")
            assert term.tui._state.pending_command_text is None, spec.key
            assert term.tui._state.pending_command_choice is not None, spec.key
            await term.escape()
            await term.escape()
        await term.send("/config max_steps")
        values = [item.value for item, _ in term.tui._command_choices.values]
        await term.choose(values.index("50"))
        path = term.service.workspace / "cade.json"
        assert json.loads(path.read_text(encoding="utf-8"))["max_steps"] == 50
        await term.escape()
        await term.send("/config max_steps")
        await term.choose(0)
        assert "max_steps" not in json.loads(path.read_text(encoding="utf-8"))

    terminal_runner(scenario)


def test_api_key_login_status_logout(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/login openai api_key")
        await term.send("e2e-dummy-key")
        assert term.service.auth.store.get("openai").access == "e2e-dummy-key"
        assert "e2e-dummy-key" not in term.transcript
        await term.send("/auth status")
        assert "openai: api_key" in term.transcript
        await term.send("/auth logout openai")
        assert term.service.auth.store.get("openai") is None
        await term.send("/connect deepseek api_key")
        await term.send("e2e-second-key")
        assert term.service.auth.store.get("deepseek") is not None
        request = term.tui._state.pending_command_choice
        assert request is not None
        assert all(
            getattr(value, "provider", "deepseek") == "deepseek"
            for _, value in request.choices
        )
        await term.escape()
        await term.send("/logout deepseek")
        assert term.service.auth.store.get("deepseek") is None

    terminal_runner(scenario)


def test_model_and_effort_native_selectors(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/login openai api_key")
        await term.send("e2e-model-key")
        await term.send("/model")
        request = term.tui._state.pending_command_choice
        assert request is not None
        index = next(
            i
            for i, (_, value) in enumerate(request.choices)
            if getattr(value, "model", None) == "gpt-5.5"
        )
        await term.choose(index)
        assert "Successfully switched" in term.transcript
        assert term.service.provider_config.model == "gpt-5.5"
        await term.send("/effort")
        request = term.tui._state.pending_command_choice
        assert request is not None
        low = next(i for i, (_, value) in enumerate(request.choices) if value == "low")
        await term.choose(low)
        assert term.service.provider_config.reasoning_effort == "low"
        for tool in term.service.base_tools:
            if tool.name == "delegate":
                assert tool.runner.provider is term.service.provider
                assert tool.runner.budget is term.service.budget
        assert term.service.compactor.provider is term.service.provider

    terminal_runner(scenario)


def test_explicit_skill_input_injects_body(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("$e2e-skill")
        await term.send("use activated skill")
        assert "E2E skill body" in json.dumps(term.service.provider.requests[-1])

    terminal_runner(scenario)


@pytest.mark.parametrize("expired", [False, True])
def test_chatgpt_login_exposes_native_model_picker(
    terminal_runner: Runner, expired: bool
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.auth.store.save(
            AuthCredential(
                type="oauth",
                provider="chatgpt",
                access="e2e-access",
                refresh="e2e-refresh",
                expires=1 if expired else 9_999_999_999,
                extra={
                    "client_id": "e2e-client",
                    "scopes": ["chatgpt.tokens.use.direct"],
                },
            )
        )
        await term.send("/model")
        request = term.tui._state.pending_command_choice
        assert request is not None
        entries = [value for _, value in request.choices if hasattr(value, "model")]
        assert entries
        assert all(entry.provider == "chatgpt" for entry in entries)
        index = next(
            i
            for i, (_, value) in enumerate(request.choices)
            if getattr(value, "model", None) == "gpt-5.5"
        )
        await term.choose(index)
        assert term.service.provider_config.provider == "chatgpt"
        assert term.service.provider_config.model == "gpt-5.5"

    terminal_runner(scenario)


@pytest.mark.parametrize("key", ["\x1b", "/stop"])
def test_interrupt_before_first_provider_chunk(
    terminal_runner: Runner, key: str
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("waiting for first chunk", wait_idle=False)
        await term.until(
            lambda: bool(term.service.provider.requests), "provider started"
        )
        started = perf_counter()
        if key.startswith("/"):
            await term.send(key, wait_idle=False)
        else:
            term.pipe.send_text(key)
        await term.until(lambda: not term.tui._state.running, "interrupted")
        assert perf_counter() - started < 1
        runs = [e for e in term.service.log.branch() if e.type == "run"]
        assert runs[-1].data["termination"] == "aborted"
        assert len(term.service.provider.requests) == 1
        assert term.tui._application.is_running

    terminal_runner(scenario)


def test_escape_immediately_after_submit_is_not_lost(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        term.pipe.send_text("instant task\r\x1b")
        await term.until(
            lambda: any(e.type == "run" for e in term.service.log.branch()),
            "immediate interruption recorded",
        )
        runs = [e for e in term.service.log.branch() if e.type == "run"]
        assert runs[-1].data["termination"] == "aborted"
        assert term.tui._application.is_running

    terminal_runner(scenario)


@pytest.mark.parametrize("exit_input", ["/quit", "\x03\x03"])
def test_exit_settles_active_run_before_closing_ui(
    terminal_runner: Runner, exit_input: str
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("active task before exit", wait_idle=False)
        if exit_input.startswith("/"):
            await term.send(exit_input, wait_idle=False)
        else:
            term.pipe.send_text(exit_input)
        await term.until(lambda: not term.tui._application.is_running, "UI exited")
        assert not term.service.running
        assert not term.tui._turn_thread.is_alive()
        runs = [
            e
            for e in SessionLog.open(term.service.log.path).branch()
            if e.type == "run"
        ]
        assert runs[-1].data["termination"] == "aborted"

    terminal_runner(scenario)


def test_alt_enter_still_steers_instead_of_aborting(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        provider = term.service.provider
        provider.delay = 0.4
        provider.batches = [
            [
                ToolCallEvent([ToolCall("read-alt", "read", {"path": "input.txt"})]),
                FinalMessage("", "tool_use"),
            ]
        ]
        (term.service.workspace / "input.txt").write_text("fixture", encoding="utf-8")
        await term.send("original task", wait_idle=False)
        term.pipe.send_text("keyboard guidance")
        await term.until(
            lambda: term.tui._input.text == "keyboard guidance", "guidance typed"
        )
        term.pipe.send_text("\x1b\r")
        await term.until(lambda: not term.tui._state.running, "guided task finished")
        assert len(provider.requests) == 2
        assert "keyboard guidance" in json.dumps(provider.requests[-1])
        runs = [e for e in term.service.log.branch() if e.type == "run"]
        assert runs[-1].data["termination"] == "completed"

    terminal_runner(scenario)


@pytest.mark.parametrize("mode", ["followup", "steer", "interrupt"])
@pytest.mark.parametrize("key,steer", [("\x1bs", True), ("\x1bq", False)])
def test_busy_shortcuts_override_enter_mode(
    terminal_runner: Runner, mode: str, key: str, steer: bool
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/queue " + mode)
        provider = term.service.provider
        provider.delay = 0.5
        provider.batches = [
            [
                ToolCallEvent(
                    [ToolCall("read-shortcut", "read", {"path": "input.txt"})]
                ),
                FinalMessage("", "tool_use"),
            ]
        ]
        (term.service.workspace / "input.txt").write_text("fixture", encoding="utf-8")
        await term.send("original task", wait_idle=False)
        await term.until(lambda: bool(provider.requests), "original request started")
        term.pipe.send_text("shortcut message")
        await term.until(
            lambda: term.tui._input.text == "shortcut message", "draft typed"
        )
        term.pipe.send_text(key)
        await term.until(lambda: not term.tui._input.text, "shortcut submitted draft")
        assert list(term.service.steering) == (["shortcut message"] if steer else [])
        assert list(term.service.pending) == ([] if steer else ["shortcut message"])
        assert term.service.cancel is not None and not term.service.cancel.cancelled
        assert term.service.busy_mode == mode
        await term.until(
            lambda: not term.tui._state.running and not term.service.pending,
            "shortcut work completed",
        )
        entries = term.service.log.branch()
        users = [entry for entry in entries if entry.type == "user"]
        assert [entry.data["text"] for entry in users] == [
            "original task",
            "shortcut message",
        ]
        assert users[-1].data.get("source", "user") == ("runtime" if steer else "user")
        assert len(provider.requests) == (2 if steer else 3)
        assert ("shortcut message" in json.dumps(provider.requests[1])) is steer
        runs = [entry for entry in entries if entry.type == "run"]
        assert len(runs) == (1 if steer else 2)
        assert all(entry.data["termination"] == "completed" for entry in runs)

    terminal_runner(scenario)


@pytest.mark.parametrize("key", ["\x1bs", "\x1bq"])
@pytest.mark.parametrize("draft", ["", "   ", "/new", "!echo hello", "$e2e-skill"])
def test_busy_shortcuts_preserve_nonmessage_input(
    terminal_runner: Runner, key: str, draft: str
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("active task", wait_idle=False)
        await term.until(
            lambda: bool(term.service.provider.requests), "request started"
        )
        term.pipe.send_text(draft)
        await term.until(lambda: term.tui._input.text == draft, "draft typed")
        term.pipe.send_text(key)
        await asyncio.sleep(0.2)
        assert term.tui._input.text == draft
        assert term.service.running
        assert term.service.cancel is not None and not term.service.cancel.cancelled
        assert not term.service.pending
        assert not term.service.steering
        assert len(term.service.provider.requests) == 1

    terminal_runner(scenario)


def test_ctrl_c_clears_draft_without_interrupting(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("waiting task", wait_idle=False)
        term.pipe.send_text("keep this draft")
        await term.until(
            lambda: term.tui._input.text == "keep this draft", "draft typed"
        )
        term.pipe.send_text("\x03")
        await term.until(lambda: not term.tui._input.text, "draft cleared")
        assert term.service.running
        assert term.service.cancel is not None and not term.service.cancel.cancelled
        assert term.tui._application.is_running
        term.pipe.send_text("\x1b")
        await term.until(lambda: not term.tui._state.running, "Esc aborted the run")
        runs = [e for e in term.service.log.branch() if e.type == "run"]
        assert runs[-1].data["termination"] == "aborted"

    terminal_runner(scenario)


def test_escape_restores_queued_input_instead_of_starting_it(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("active task", wait_idle=False)
        await term.send("queued task", wait_idle=False)
        await term.until(lambda: bool(term.service.pending), "follow-up queued")
        await term.until(
            lambda: bool(term.service.provider.requests), "active task started"
        )
        term.pipe.send_text("\x1b")
        await term.until(lambda: not term.tui._state.running, "active task aborted")
        assert not term.service.pending
        assert not term.service.steering
        assert term.tui._input.text == "queued task"
        assert len(term.service.provider.requests) == 1
        users = [e.data["text"] for e in term.service.log.branch() if e.type == "user"]
        assert users == ["active task"]

    terminal_runner(scenario)


def test_double_escape_opens_history_only_when_idle_and_input_empty(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("history task")
        session = term.service.log.session_id
        term.pipe.send_text("\x1b")
        await asyncio.sleep(0.7)
        assert term.tui._state.pending_command_choice is None
        term.pipe.send_text("\x1b\x1b")
        await term.until(
            lambda: term.tui._state.pending_command_choice is not None,
            "history picker opened",
        )
        assert term.service.log.session_id == session
        assert any(
            "history task" in str(label)
            for label, _ in term.tui._state.pending_command_choice.choices
        )
        await term.escape()
        assert term.tui._state.pending_command_choice is None
        term.pipe.send_text("draft\x1b\x1b")
        await asyncio.sleep(0.7)
        assert term.tui._state.pending_command_choice is None
        assert term.tui._input.text == "draft"

    terminal_runner(scenario)


def test_resume_picker_hides_unsent_conversations(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        assert not list(term.service.sessions_dir.glob("session-*"))
        await term.send("/new")
        await term.send("/new")
        assert not list(term.service.sessions_dir.glob("session-*"))
        await term.send("/resume")
        assert term.tui._state.pending_command_choice is None
        assert "No conversations found" in term.transcript
        await term.send("saved task")
        saved = term.service.log.session_id
        await term.send("/new")
        await term.send("/new")
        await term.send("/resume")
        request = term.tui._state.pending_command_choice
        assert request is not None
        assert [value.id for _, value in request.choices] == [saved]
        await term.choose()
        assert term.service.log.session_id == saved
        await term.send("/new")
        await term.send("/continue")
        await term.choose()
        assert term.service.log.session_id == saved

    terminal_runner(scenario)


def test_login_method_selects_provider_and_masks_api_key(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/login")
        await term.choose(1)
        await term.choose(0)
        assert term.tui._state.pending_command_text.secret
        term.pipe.send_text("e2e-hidden-secret")
        await term.until(
            lambda: term.tui._input.text == "e2e-hidden-secret", "secret typed"
        )
        await asyncio.sleep(0.1)
        assert "e2e-hidden-secret" not in "".join(
            term.tui._application.output.fragments
        )
        term.pipe.send_text("\r")
        await term.until(
            lambda: term.service.auth.store.get("openai") is not None,
            "credential saved",
        )
        assert "e2e-hidden-secret" not in term.tui._input.buffer.history.get_strings()
        provider = term.service.provider
        await term.send("/logout")
        await term.choose()
        assert term.service.auth.store.get("openai") is None
        before = len(provider.requests)
        await term.send("unsent while disconnected")
        assert term.tui._input.text == "unsent while disconnected"
        assert "Missing openai login" in term.transcript
        assert len(provider.requests) == before

    terminal_runner(scenario)


def test_verbosity_switch_and_picker_do_not_change_model_effort(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        assert term.tui._state.verbosity == "auto"
        effort = term.service.provider_config.reasoning_effort
        await term.send("/verbose high")
        assert term.tui._state.verbosity == "high"
        assert not term.tui._state.thinking_collapsed
        assert not term.tui._state.tool_collapsed
        await term.send("/verbose")
        assert term.tui._state.pending_command_choice is not None
        await term.choose(0)
        assert term.tui._state.verbosity == "auto"
        assert term.tui._state.thinking_collapsed
        assert term.tui._state.tool_collapsed
        assert term.service.provider_config.reasoning_effort == effort

    terminal_runner(scenario)


def test_thinking_picker_changes_reasoning_not_visibility(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        collapsed = term.tui._state.thinking_collapsed
        await term.send("/thinking")
        await term.choose(0)
        assert term.service.provider_config.reasoning_effort == "none"
        assert term.tui._state.thinking_collapsed == collapsed
        await term.send("/thinking high")
        assert term.service.provider_config.reasoning_effort == "high"
        term.pipe.send_text("\x14")
        await term.until(
            lambda: term.tui._state.thinking_collapsed != collapsed,
            "visibility toggled",
        )
        assert term.service.provider_config.reasoning_effort == "high"

    terminal_runner(scenario)


def test_model_query_filters_authenticated_models(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.auth.store.save(
            AuthCredential(
                type="oauth",
                provider="chatgpt",
                access="e2e-access",
                refresh="e2e-refresh",
            )
        )
        await term.send("/model gpt-5.5")
        assert term.service.provider_config.provider == "chatgpt"
        await term.send("/model luna")
        request = term.tui._state.pending_command_choice
        assert request is not None
        entries = [value for _, value in request.choices if hasattr(value, "model")]
        assert entries and all("luna" in entry.model for entry in entries)
        await term.escape()
        assert term.service.provider_config.model == "gpt-5.5"

    terminal_runner(scenario)


def test_queue_picker_and_side_question_form(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("initial task")
        await term.send("/queue")
        await term.choose(1)
        assert term.service.busy_mode == "steer"
        original = term.service.log.path.read_bytes()
        await term.send("/btw")
        await term.send("side question")
        assert "E2E answer" in term.transcript
        assert len(term.service.provider.requests) == 2
        assert term.service.log.path.read_bytes() == original

    terminal_runner(scenario)


def test_goal_lifecycle(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/goal test lifecycle")
        await term.send("/goal")
        assert "active: test lifecycle" in term.transcript
        await term.send("/goal done")
        assert term.service.goals.load().status == "done"
        await term.send("/goal clear")
        assert term.service.goals.load() is None

    terminal_runner(scenario)


def test_permission_picker_grants_only_current_session(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.automatic_approver = None
        await term.send("/mode ask")
        term.service.provider.batches = [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            "write-approved",
                            "write",
                            {"path": "approved.txt", "content": "approved"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ]
        ]
        await term.send("write a file with permission", wait_idle=False)
        await term.until(
            lambda: term.tui._state.pending_hitl is not None, "permission requested"
        )
        assert [value for value, _ in term.tui._approval_choices.values] == [
            "Allow once",
            "Allow for session",
            "Deny",
        ]
        term.pipe.send_text("\x1b[B\r")
        await term.until(lambda: not term.tui._state.running, "approved task completed")
        assert (term.service.workspace / "approved.txt").read_text(
            encoding="utf-8"
        ) == "approved"
        assert term.service.policy.grants.describe()
        await term.send("/new")
        assert not term.service.policy.grants.describe()

    terminal_runner(scenario)


def test_queue_runs_followup(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/queue queued task")
        await term.until(
            lambda: len(term.service.provider.requests) == 1, "queued task ran"
        )
        await term.until(lambda: not term.tui._state.running, "queue drained")
        assert not term.service.pending
        users = [e.data["text"] for e in term.service.log.branch() if e.type == "user"]
        assert users == ["queued task"]

    terminal_runner(scenario)


@pytest.mark.parametrize("command", ["/rename", "/mcp"])
def test_deferred_commands_do_not_mutate_session_or_files(
    terminal_runner: Runner, command: str
) -> None:
    async def scenario(term: Terminal) -> None:
        path = term.service.workspace / "target.txt"
        path.write_text("before", encoding="utf-8")
        term.service.provider.batches = [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            "write-1",
                            "write",
                            {"path": "target.txt", "content": "after"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ]
        ]
        await term.send("update target.txt")
        assert path.read_text(encoding="utf-8") == "after"
        original = term.service.log.path.read_bytes()
        await term.send(command)
        assert "Unknown command" in term.transcript
        assert path.read_text(encoding="utf-8") == "after"
        assert term.service.log.path.read_bytes() == original
        assert term.service.snapshots.list()

    terminal_runner(scenario)


def test_compaction_writes_summary_and_retains_recent_task(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        # 用磁盘历史作为前置条件，避免输入框粘贴十万字符。
        term.service.log.append("user", {"text": "old context " * 20_000})
        term.service.log.append(
            "assistant", {"blocks": [{"type": "text", "text": "old answer"}]}
        )
        await term.send("recent task")
        await term.send("/compact Preserve API contracts")
        assert "Preserve API contracts" in json.dumps(
            term.service.provider.requests[-1]
        )
        reopened = SessionLog.open(term.service.log.path)
        summaries = [e for e in reopened.branch() if e.type == "compaction"]
        assert len(summaries) == 1
        assert summaries[0].data["summary"] == "E2E answer"
        assert any(
            e.type == "user" and e.data["text"] == "recent task"
            for e in reopened.branch()
        )
        assert len(term.service.provider.requests) == 2

    terminal_runner(scenario)


@pytest.mark.parametrize("command", ["/rollover", "/new-context"])
def test_rollover_requires_note_and_uses_handoff(
    terminal_runner: Runner, command: str
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("first task")
        await term.send(command)
        assert not any(e.type == "window" for e in term.service.log.branch())
        assert "NOTE.md" in term.transcript
        (term.service.workspace / "NOTE.md").write_text(
            "Verified handoff", encoding="utf-8"
        )
        await term.send(command)
        windows = [e for e in term.service.log.branch() if e.type == "window"]
        assert len(windows) == 1
        assert "Verified handoff" in json.dumps(windows[0].data)

    terminal_runner(scenario)


@pytest.mark.parametrize("control", ["/steer injected guidance", "/stop"])
def test_busy_control_reaches_core_loop(terminal_runner: Runner, control: str) -> None:
    async def scenario(term: Terminal) -> None:
        provider = term.service.provider
        provider.delay = 0.4
        provider.batches = [
            [
                ToolCallEvent([ToolCall("read-1", "read", {"path": "input.txt"})]),
                FinalMessage("", "tool_use"),
            ]
        ]
        (term.service.workspace / "input.txt").write_text("fixture", encoding="utf-8")
        await term.send("running task", wait_idle=False)
        await term.until(lambda: bool(provider.requests), "core loop started")
        await term.send(control, wait_idle=False)
        await term.until(lambda: not term.tui._state.running, "core loop stopped")
        runs = [e for e in term.service.log.branch() if e.type == "run"]
        assert runs
        if control == "/stop":
            assert len(provider.requests) == 1
            assert runs[-1].data["termination"] == "aborted"
        else:
            assert len(provider.requests) == 2
            assert "injected guidance" in json.dumps(provider.requests[-1])
            assert not term.service.steering

    terminal_runner(scenario)


@pytest.mark.parametrize("mode", ["followup", "steer", "interrupt"])
def test_busy_queue_modes(terminal_runner: Runner, mode: str) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("/queue " + mode)
        assert term.service.busy_mode == mode
        provider = term.service.provider
        provider.delay = 0.3
        provider.batches = [
            [
                ToolCallEvent([ToolCall("read-queue", "read", {"path": "input.txt"})]),
                FinalMessage("", "tool_use"),
            ]
        ]
        (term.service.workspace / "input.txt").write_text("fixture", encoding="utf-8")
        await term.send("original task", wait_idle=False)
        await term.until(lambda: term.service.running, "original task started")
        await term.send("busy message", wait_idle=False)
        await term.until(
            lambda: not term.tui._state.running and not term.service.pending,
            "busy input consumed",
        )
        users = [e for e in term.service.log.branch() if e.type == "user"]
        assert [e.data["text"] for e in users] == ["original task", "busy message"]
        if mode == "steer":
            assert users[-1].data.get("source") == "runtime"
            assert len(provider.requests) == 2
        else:
            assert users[-1].data.get("source", "user") == "user"
        if mode == "interrupt":
            runs = [e for e in term.service.log.branch() if e.type == "run"]
            assert runs[0].data["termination"] == "aborted"
            assert runs[-1].data["termination"] == "completed"

    terminal_runner(scenario)


def test_fork_picker_creates_independent_conversation(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("first task")
        await term.send("second task")
        source = term.service.log.path
        original = source.read_bytes()
        await term.send("/fork")
        await term.choose(0)
        assert term.service.log.path != source
        assert term.tui._input.text == "first task"
        assert not term.service.log.path.exists()
        await term.send("alternate task")
        reopened = SessionLog.open(term.service.log.path)
        assert [e.data["text"] for e in reopened.branch() if e.type == "user"] == [
            "alternate task"
        ]
        assert source.read_bytes() == original

    terminal_runner(scenario)


def test_file_reference_keeps_file_contents_as_data(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        body = "$not_a_skill @not-a-file\nunique attached content"
        (term.service.workspace / "attached.txt").write_text(body, encoding="utf-8")
        await term.send("inspect @attached.txt")
        user = next(e for e in term.service.log.branch() if e.type == "user")
        assert str(user.data["text"]).count(body) == 1
        assert "[error]" not in term.transcript

    terminal_runner(scenario)


@pytest.mark.parametrize("action", ["escape", "exit"])
def test_shell_shortcut_cancels_and_settles(
    terminal_runner: Runner, action: str
) -> None:
    class WaitingShell:
        def __init__(self) -> None:
            self.name = "powershell" if sys.platform == "win32" else "bash"
            self.description = "Wait for cancellation"
            self.parameters = {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }
            self.started = False
            self.stopped = False

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None = None
        ) -> ToolResult:
            assert signal is not None
            self.started = True
            await signal.wait()
            self.stopped = True
            return ToolResult("cancelled", is_error=True)

    async def scenario(term: Terminal) -> None:
        shell = WaitingShell()
        term.service.base_tools = (shell,)
        await term.send("!echo waiting", wait_idle=False)
        await term.until(lambda: shell.started, "shell started")
        if action == "escape":
            await term.escape()
            await term.until(lambda: not term.tui._state.running, "shell cancelled")
        else:
            await term.send("/quit", wait_idle=False)
            await term.until(lambda: not term.tui._application.is_running, "UI exited")
        assert shell.stopped
        assert not term.service.running
        worker = term.tui._turn_thread
        assert worker is not None
        await asyncio.to_thread(worker.join, 2)
        assert not worker.is_alive()
        result = next(
            e for e in reversed(term.service.log.branch()) if e.type == "tool_result"
        )
        assert result.data["status"] == "error"
        history = term.service.recovery.store.history(term.service.log.session_id)
        assert history.head_id == term.service.log.head_id

    terminal_runner(scenario)


@pytest.mark.parametrize(
    "command",
    [
        "/mode invalid",
        "/context-window 0",
        "/thinking invalid",
        "/effort invalid",
        "/resume missing",
    ],
)
def test_invalid_arguments_preserve_session(
    terminal_runner: Runner, command: str
) -> None:
    async def scenario(term: Terminal) -> None:
        original = term.service.log.session_id
        await term.send(command)
        assert "error" in term.transcript.lower()
        assert term.service.log.session_id == original
        await term.send("still usable")
        assert "E2E answer" in term.transcript

    terminal_runner(scenario)


@pytest.mark.parametrize("dismiss", [False, True])
def test_question_answer_and_dismissal(terminal_runner: Runner, dismiss: bool) -> None:
    async def scenario(term: Terminal) -> None:
        provider = term.service.provider
        assert isinstance(provider, ScriptedProvider)
        provider.batches = [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="question",
                            name="question",
                            input={
                                "questions": [
                                    {
                                        "question": "Which workflow?",
                                        "header": "Workflow",
                                        "options": [
                                            {
                                                "label": "Build",
                                                "description": "Implement the change",
                                            }
                                        ],
                                    }
                                ]
                            },
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ]
        ]
        await term.send("/mode plan")
        await term.send("Choose a workflow", wait_idle=False)
        await term.until(
            lambda: term.tui._state.pending_question_choice is not None,
            "question visible",
        )
        if dismiss:
            await term.escape()
        else:
            term.pipe.send_text("\r")
        await term.until(lambda: not term.tui._state.running, "question finished")
        runs = [entry for entry in term.service.log.branch() if entry.type == "run"]
        assert runs[-1].data["termination"] == ("aborted" if dismiss else "completed")
        if dismiss:
            assert len(provider.requests) == 1
            assert "cancelled" in term.transcript
        else:
            assert len(provider.requests) == 2
            assert any(
                '"Which workflow?"="Build"' in str(message.get("content", ""))
                for message in provider.requests[-1]
                if message["role"] == "tool"
            )

    terminal_runner(scenario)
