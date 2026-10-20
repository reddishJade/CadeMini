"""Regression coverage for Cade Mini's independent terminal interaction layer."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from prompt_toolkit.document import Document

from cade_mini.ai.events import FinalMessage, ReasoningDelta, TextDelta
from cade_mini.app.provider import ProviderConfig
from cade_mini.core.compaction import Compactor
from cade_mini.core.log import SessionLog
from cade_mini.core.loop import (
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
    UsageEvent,
)
from cade_mini.core.request import Budget
from cade_mini.core.types import ToolCallBlock
from cade_mini.extensions.goal import GoalStore
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.hosts.interaction import InteractionService
from cade_mini.hosts.tui.app import CadeTui
from cade_mini.hosts.tui.completion import CadeCompleter
from cade_mini.hosts.tui.state import Transcript
from cade_mini.safety.policy import WorkspacePolicy


class FakeProvider:
    model = "test-model"
    transport = "openai"
    context_window = 200_000

    async def stream(self, messages, tools, options=None):
        yield ReasoningDelta(chunk="thinking")
        yield TextDelta(chunk="Answer")
        yield FinalMessage(content="Answer", stop_reason="end_turn")


def service(tmp_path: Path) -> InteractionService:
    sessions = tmp_path / ".cade-mini" / "sessions"
    log = SessionLog.create(sessions)
    log.append("system", {"text": "You are a coding agent."})
    provider = FakeProvider()
    return InteractionService(
        log=log,
        sessions_dir=sessions,
        system_prompt="You are a coding agent.",
        provider=provider,
        provider_config=ProviderConfig(provider="openai", model="test-model"),
        tools=(),
        skills=(),
        workspace=tmp_path,
        snapshots=SnapshotStore(tmp_path),
        goals=GoalStore(tmp_path),
        policy=WorkspacePolicy(tmp_path),
        budget=Budget(context_window=200_000),
        context=tuple,
        max_steps=4,
        compactor=Compactor(provider),
        hooks=None,
        window=None,
    )


def completions(engine: CadeCompleter, source: str) -> list[str]:
    return [item.text for item in engine.get_completions(Document(source), object())]


def test_command_completion_and_fuzzy_scoring(tmp_path: Path) -> None:
    app = service(tmp_path)
    comp = CadeCompleter(app)
    assert "/context" in completions(comp, "/con")
    assert "/model" in completions(comp, "/mod")
    assert "act" in completions(comp, "/mode a")


def test_workspace_file_completion(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hi')\n")
    folder = tmp_path / ".git"
    folder.mkdir()
    (folder / "secret").write_text("hidden")
    comp = CadeCompleter(service(tmp_path))
    assert "@hello.py" in completions(comp, "Review @hello")
    assert "@.git/secret" not in completions(comp, "Review @secret")


@pytest.mark.asyncio
async def test_session_commands_update_host_log_without_losing_history(
    tmp_path: Path,
) -> None:
    app = service(tmp_path)
    app.log.append("user", {"text": "first"})
    old = app.log.session_id
    picker = await app.command("/fork")
    assert picker.picker == "fork"
    assert any(choice == app.log.branch()[-1].id for choice, _ in app.choices("fork"))
    fork = await app.command("/fork " + app.log.branch()[-1].id)
    assert fork.changed_session and fork.draft == "first"
    assert app.log.session_id != old
    result = await app.command("/resume " + old)
    assert result.changed_session
    assert app.log.session_id == old
    assert (await app.command("/mode build")).lines == ("Mode: build",)
    assert app.policy.mode == "build"
    assert (await app.command("/context-window 150k")).lines == ("Window: 150,000",)


@pytest.mark.asyncio
async def test_run_interaction_streams_reasoning_and_final(tmp_path: Path) -> None:
    app = service(tmp_path)
    received = []
    result = await app.run_task("question", received.append, _allow)
    assert result.termination == "completed"
    assert any(isinstance(e, ReasoningDeltaEvent) for e in received)
    assert any(isinstance(e, TextDeltaEvent) for e in received)
    assert any(item.type == "user" for item in app.log.branch())
    assert not app.running


async def _allow(call: ToolCallBlock) -> bool:
    return True


def test_transcript_replay_and_folded_tools(tmp_path: Path) -> None:
    app = service(tmp_path)
    app.log.append("user", {"text": "read hello"})
    app.log.append(
        "assistant",
        {
            "blocks": [
                {
                    "type": "tool_call",
                    "id": "c1",
                    "name": "read",
                    "args": {"path": "hello"},
                }
            ],
        },
    )
    app.log.append(
        "tool_result", {"call_id": "c1", "content": "hello world", "status": "ok"}
    )
    state = Transcript(tmp_path, app.log.session_id)
    state.restore(app.log)
    assert "read" in "\n".join(state.ansi_lines(100))
    assert "hello world" not in "\n".join(state.ansi_lines(100))
    state.toggle_tools()
    assert "hello world" in "\n".join(state.ansi_lines(100))
    state.handle(ToolStartEvent("x", "bash", {"command": "pwd"}))
    state.handle(ToolEndEvent("x", "failure", True))
    assert any(item.error for item in state.entries if item.call_id == "x")


def test_tui_initialization_is_inline(tmp_path: Path) -> None:
    terminal = CadeTui(service(tmp_path))
    assert not terminal.app.full_screen
    assert not terminal.app.mouse_support()
    assert terminal.app.layout is not None


@pytest.mark.asyncio
async def test_side_question_is_independent_and_unpersisted(
    tmp_path, monkeypatch
) -> None:
    app = service(tmp_path)
    app.log.append("user", {"text": "main task"})
    before = app.log.entries()
    text_chunks = []
    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider", lambda _config: FakeProvider()
    )
    answer = await app.side_question("what is the status?", text_chunks.append)
    assert answer == "Answer"
    assert "".join(text_chunks) == "Answer"
    assert app.log.entries() == before


@pytest.mark.asyncio
async def test_busy_queue_policy_and_command_aliases(tmp_path: Path) -> None:
    app = service(tmp_path)
    mode = await app.command("/queue interrupt")
    assert app.busy_mode == "interrupt"
    assert "interrupt" in mode.lines[0]
    assert "interrupt" in dict(app.choices("queue"))
    answer = await app.command("/btw Explain status")
    assert answer.side_question == "Explain status"
    result = await app.command("/queue Review tests")
    assert "Queued" in result.lines[0]
    assert app.pending.pop() == "Review tests"
    result = await app.command("/clear")
    assert result.changed_session


def test_settings_service_schema_and_atomic_write(tmp_path: Path) -> None:
    from cade_mini.app.config import MiniConfig
    from cade_mini.hosts.interaction_settings import SettingsService

    settings = SettingsService(tmp_path)
    assert "provider" in dict(settings.choices())
    settings.save("provider", "chatgpt")
    settings.save("model", "test-model")
    assert MiniConfig.load(tmp_path).model == "test-model"
    with pytest.raises(ValueError):
        settings.save("mode", "this-is-invalid")
    assert MiniConfig.load(tmp_path).mode == "act"


def test_api_key_is_written_to_credential_store_only(tmp_path: Path) -> None:
    from cade_mini.app.auth import JsonCredentialStore
    from cade_mini.hosts.interaction_settings import AuthenticationService

    auth = AuthenticationService()
    auth.store = JsonCredentialStore(tmp_path / "private-auth.json")
    auth.save_api_key("openai", "top-secret")
    assert auth.store.get("openai").access == "top-secret"
    assert not (tmp_path / "cade-mini.json").exists()
    assert auth.logout("openai") == "Logged out: openai"
    assert auth.store.get("openai") is None


def test_side_answer_does_not_absorb_main_stream_deltas(tmp_path: Path) -> None:
    state = Transcript(tmp_path, "s1")
    side = state.start_side("independent question")
    state.side_delta(side, "Side A")
    state.handle(TextDeltaEvent("Main A"))
    state.side_delta(side, " Side B")
    state.handle(TextDeltaEvent(" Main B"))
    state.finish_side(side)
    assert side.text == "Side A Side B"
    main = [item for item in state.entries if item.kind == "assistant"]
    assert len(main) == 1
    assert main[0].text == "Main A Main B"


@pytest.mark.asyncio
async def test_live_terminal_help_and_keyboard_exit(tmp_path: Path) -> None:
    """Exercise the actual prompt-toolkit event loop with synthetic terminal input."""
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        terminal = CadeTui(service(tmp_path), input=pipe, output=DummyOutput())
        task = asyncio.create_task(terminal.app.run_async())
        try:
            await asyncio.sleep(0.08)
            pipe.send_text("/help\r")
            await asyncio.sleep(0.1)
            pipe.send_bytes(b"\x11")  # Ctrl+Q
            await asyncio.wait_for(task, timeout=4)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert any(
            "Commands" in item.text or "/help" in item.text
            for item in terminal.transcript.entries
        )


def test_replay_keeps_historical_turns_after_compaction_and_rollover(
    tmp_path: Path,
) -> None:
    app = service(tmp_path)
    app.log.append("user", {"text": "old user question"})
    app.log.append(
        "assistant",
        {"blocks": [{"type": "text", "text": "old assistant answer"}]},
    )
    current = app.log.append("user", {"text": "new user question"})
    app.log.append(
        "assistant",
        {"blocks": [{"type": "text", "text": "new assistant answer"}]},
    )
    app.log.append(
        "compaction",
        {
            "first_kept_id": current.id,
            "summary": "short model summary",
            "tokens_before": 2000,
            "model": "test-model",
            "provider": "openai",
        },
    )
    app.log.append(
        "window",
        {"start_id": current.id, "reason": "manual", "handoff": "new model context"},
    )

    state = Transcript(tmp_path, app.log.session_id)
    state.restore(app.log)
    timeline = [item.text for item in state.entries]
    assert "old user question" in timeline
    assert "old assistant answer" in timeline
    assert "new user question" in timeline
    assert "new assistant answer" in timeline
    assert "Context compacted" in timeline
    assert "Context rolled over" in timeline


@pytest.mark.asyncio
async def test_tree_inspects_any_event_without_forking(tmp_path: Path) -> None:
    app = service(tmp_path)
    app.log.append("user", {"text": "hello"})
    assistant = app.log.append(
        "assistant",
        {"blocks": [{"type": "text", "text": "detailed output"}]},
    )
    session_id = app.log.session_id
    before = app.log.entries()
    assert (await app.command("/tree")).picker == "tree"
    assert assistant.id in dict(app.choices("tree"))
    result = await app.command(f"/tree {assistant.id}")
    assert not result.changed_session
    assert "detailed output" in result.lines[0]
    assert app.log.session_id == session_id
    assert app.log.entries() == before


def test_expanded_tool_diff_has_complete_changes(tmp_path: Path) -> None:
    state = Transcript(tmp_path, "test")
    before = "".join(f"old line {i}\n" for i in range(100))
    after = "".join(f"new line {i}\n" for i in range(100))
    state.handle(
        ToolStartEvent(
            "edit-1",
            "edit",
            {"path": "example.py", "old_text": before, "new_text": after},
        )
    )
    state.handle(ToolEndEvent("edit-1", "Applied", False))
    folded = "\n".join(state.ansi_lines(100))
    assert "new line 99" not in folded
    state.toggle_tools()
    expanded = "\n".join(state.ansi_lines(100))
    assert "-old line 99" in expanded
    assert "+new line 99" in expanded
    assert "Applied" in expanded
    assert "✓ edit" in expanded


@pytest.mark.asyncio
async def test_approval_preview_scrolls_past_initial_page(tmp_path: Path) -> None:
    terminal = CadeTui(service(tmp_path))
    terminal._approval_call = ToolCallBlock(
        id="write-1",
        name="write",
        args={"path": "long.txt", "content": "\n".join(str(i) for i in range(100))},
    )
    terminal._approval = asyncio.get_running_loop().create_future()
    terminal.transcript.toggle_tools()
    assert "  99" in terminal._approval_text()
    assert len(terminal._approval_text().splitlines()) > terminal._approval_height()
    terminal._scroll_approval(1)
    assert terminal._approval_preview.vertical_scroll > 0
    terminal._scroll_approval(-(10**6))
    assert terminal._approval_preview.vertical_scroll == 0
    terminal._close_approval()


def test_usage_event_updates_live_tui_status(tmp_path: Path) -> None:
    terminal = CadeTui(service(tmp_path))
    terminal._event(UsageEvent(1500, 250))
    assert terminal.service.last_usage == {"input": 1500, "output": 250}
    assert "1,500" in str(terminal._status_text())


@pytest.mark.asyncio
async def test_denied_tool_has_named_start_and_completion_events(
    tmp_path: Path,
) -> None:
    from cade_mini.ai.events import ToolCall, ToolCallEvent

    class CallingProvider(FakeProvider):
        calls = 0

        async def stream(self, messages, tools, options=None):
            self.calls += 1
            if self.calls == 1:
                yield ToolCallEvent(
                    [
                        ToolCall(
                            id="refused",
                            name="write",
                            input={"path": "x.txt", "content": "x"},
                        )
                    ]
                )
                yield FinalMessage(content="", stop_reason="tool_use")
            else:
                yield FinalMessage(content="done", stop_reason="end_turn")

    app = service(tmp_path)
    app.provider = CallingProvider()
    events: list[object] = []

    async def deny(_call: ToolCallBlock) -> bool:
        return False

    await app.run_task("please write", events.append, deny)
    starts = [e for e in events if isinstance(e, ToolStartEvent)]
    ends = [e for e in events if isinstance(e, ToolEndEvent)]
    assert len(starts) == len(ends) == 1
    assert starts[0].name == "write"
    assert ends[0].is_error


@pytest.mark.asyncio
async def test_live_terminal_tool_and_thinking_shortcuts(tmp_path: Path) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        terminal = CadeTui(service(tmp_path), input=pipe, output=DummyOutput())
        task = asyncio.create_task(terminal.app.run_async())
        try:
            await asyncio.sleep(0.08)
            pipe.send_bytes(b"\x0f")  # Ctrl+O
            await asyncio.sleep(0.1)
            assert terminal.transcript.tools_expanded
            pipe.send_bytes(b"\x14")  # Ctrl+T
            await asyncio.sleep(0.1)
            assert terminal.transcript.thinking_expanded
            pipe.send_bytes(b"\x11")  # Ctrl+Q
            await asyncio.wait_for(task, timeout=4)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_rename_uses_validated_metadata_and_updates_session_title(
    tmp_path: Path,
) -> None:
    from cade_mini.core.log import session_title

    svc = service(tmp_path)
    svc.log.append("user", {"text": "old prompt"})
    assert (await svc.command("/rename Useful notes")).lines == (
        "Session renamed: Useful notes",
    )
    assert session_title(svc.log.branch()) == "Useful notes"
    metas = [entry for entry in svc.log.branch() if entry.type == "meta"]
    assert len(metas) == 2
    assert metas[0].data["session"] == metas[1].data["session"]
    assert metas[0].data["created"] == metas[1].data["created"]


def test_model_and_login_options_match_current_provider_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade_mini.app.auth import JsonCredentialStore

    svc = service(tmp_path)
    svc.auth.store = JsonCredentialStore(tmp_path / "private-auth.json")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert not svc.choices("model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    models = dict(svc.choices("model"))
    assert any(name.startswith("openai/") for name in models)
    assert all(not name.startswith(("chatgpt/", "openai-codex/")) for name in models)
    assert set(dict(svc.auth.choices())) == {"chatgpt", "openai"}


def test_settings_batch_is_atomic_and_validated(tmp_path: Path) -> None:
    from cade_mini.app.config import MiniConfig
    from cade_mini.hosts.interaction_settings import SettingsService

    settings = SettingsService(tmp_path)
    settings.save_many({"provider": "chatgpt", "model": "gpt-6-luna"})
    before = (tmp_path / "cade-mini.json").read_text()
    assert MiniConfig.load(tmp_path).model == "gpt-6-luna"
    with pytest.raises(ValueError):
        settings.save_many({"provider": "openai", "mode": "invalid"})
    assert (tmp_path / "cade-mini.json").read_text() == before


@pytest.mark.asyncio
async def test_nested_tool_approval_uses_terminal_host_and_auto_review(
    tmp_path: Path,
) -> None:
    app = service(tmp_path)
    received: list[str] = []

    class Router:
        def __init__(self) -> None:
            self.target = _allow

    router = Router()
    app.approval_router = router

    async def reviewer(call: ToolCallBlock) -> bool:
        received.append(call.name)
        return True

    app.automatic_approver = reviewer
    terminal = CadeTui(app)
    call = ToolCallBlock(
        id="approval-1",
        name="write",
        args={"path": "notes.txt", "content": "hello"},
    )
    assert await router.target(call) is True
    assert received == ["write"]
    assert terminal.service is app


def test_markdown_renders_during_stream_not_only_at_end(tmp_path: Path) -> None:
    state = Transcript(tmp_path, "stream")
    state.begin_run()
    state.handle(TextDeltaEvent("**emphasized**"))
    assert not state.entries[-1].finished
    rendered = "\n".join(state.ansi_lines(100))
    assert "emphasized" in rendered
    assert "**emphasized**" not in rendered


def test_palette_fuzzy_search_navigation_and_empty_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    terminal = CadeTui(service(tmp_path))
    terminal._open_picker("model")
    assert terminal.app.layout.current_control == terminal._picker_search.control
    assert len(terminal._picker.values) > 1

    terminal._picker_search.text = "gpt-6-luna"
    matches = terminal._picker.values
    assert any(choice.endswith("gpt-6-luna") for choice, _ in matches)
    assert len(matches) < len(terminal._picker_source)
    terminal._move_picker(1)
    assert (
        terminal._picker.current_value == matches[terminal._picker._selected_index][0]
    )

    terminal._picker_search.text = "zzzzzz_unavailable_model"
    assert terminal._picker.values == [("", "No matches")]
    terminal._pick()
    assert terminal._picker_kind == "model"
    terminal._close_picker()
    assert terminal._picker_kind is None


def test_model_picker_allows_custom_model_without_unusable_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    terminal = CadeTui(service(tmp_path))
    terminal._open_picker("model")
    terminal._picker_search.text = "custom model"
    assert terminal._picker.current_value == "__custom__"
    terminal._pick()
    assert terminal._picker_kind is None
    assert terminal._input.text == (
        f"/model {terminal.service.provider_config.provider}/"
    )


def test_switching_model_updates_subagent_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade_mini.extensions.subagents import (
        DelegateTool,
        SubagentRunner,
        SubagentTool,
    )

    host = service(tmp_path)
    runner = SubagentRunner(
        workspace=tmp_path,
        provider=host.provider,
        tools=(),
        budget=host.budget,
        sessions_dir=host.sessions_dir,
        system_prompt=host.system_prompt,
        policy=host.policy,
        approve=_allow,
    )
    host.base_tools = (SubagentTool(runner=runner), DelegateTool(runner=runner))

    class NewProvider(FakeProvider):
        model = "gpt-6-luna"
        context_window = 300_000

    replacement = NewProvider()
    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: replacement,
    )
    host.select_model("openai/gpt-6-luna", "medium")
    assert host.provider is replacement
    assert runner.provider is replacement
    assert runner.budget is host.budget
    assert runner.budget.context_window == 300_000


def test_successful_exploration_calls_fold_into_one_progress_line(
    tmp_path: Path,
) -> None:
    state = Transcript(tmp_path, "session")
    calls = [
        ("read", {"path": "src/a.py"}),
        ("read", {"path": "src/b.py"}),
        ("grep", {"pattern": "TODO"}),
    ]
    for i, (name, args) in enumerate(calls):
        call_id = f"explore-{i}"
        state.handle(ToolStartEvent(call_id, name, args))
        state.handle(ToolEndEvent(call_id, f"result {i}", False))

    folded = "\n".join(state.ansi_lines(100))
    assert "Explored 3" in folded
    assert "read ×2" in folded
    assert "grep ×1" in folded
    assert "src/a.py" not in folded

    state.toggle_tools()
    expanded = "\n".join(state.ansi_lines(100))
    assert "Explored 3" not in expanded
    assert "src/a.py" in expanded
    assert "src/b.py" in expanded
    assert "TODO" in expanded


def test_failed_exploration_stays_visible_in_folded_view(tmp_path: Path) -> None:
    state = Transcript(tmp_path, "session")
    state.handle(ToolStartEvent("bad", "read", {"path": "missing.py"}))
    state.handle(ToolEndEvent("bad", "File not found", True))
    assert "File not found" in "\n".join(state.ansi_lines(80))


def test_completed_markdown_is_cached_across_future_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade_mini.hosts.tui import state as transcript_module

    transcript = Transcript(tmp_path, "cache")
    calls: list[str] = []
    renderer = transcript_module.markdown_ansi_lines

    def tracked(text: str, width: int | None = None) -> list[str]:
        calls.append(text)
        return renderer(text, width)

    monkeypatch.setattr(transcript_module, "markdown_ansi_lines", tracked)
    transcript.begin_run()
    transcript.handle(TextDeltaEvent("Completed **answer**"))
    transcript.end_run()
    transcript.ansi_lines(80)
    assert calls == ["Completed **answer**"]

    transcript.add_user("Next question")
    transcript.ansi_lines(80)
    assert calls == ["Completed **answer**"]

    transcript.ansi_lines(60)
    assert len(calls) == 2


def test_main_run_completion_does_not_finish_concurrent_btw(
    tmp_path: Path,
) -> None:
    transcript = Transcript(tmp_path, "parallel")
    side = transcript.start_side("Another question")
    transcript.begin_run()
    transcript.handle(TextDeltaEvent("Main answer"))
    transcript.end_run()
    assert not side.finished
    transcript.side_delta(side, "Partial reply")
    assert not side.finished
    transcript.finish_side(side)
    assert side.finished


@pytest.mark.asyncio
async def test_question_tool_collects_one_choice_and_custom_answer(
    tmp_path: Path,
) -> None:
    from cade_mini.hosts.interaction_question import Question, QuestionTool

    terminal = CadeTui(service(tmp_path))
    assert any(tool.name == "question" for tool in terminal.service.tools)
    prompt = Question(
        "Which action?",
        options=(("Inspect", "Read only"), ("Apply", "Write files")),
    )
    pending = asyncio.create_task(terminal._ask_questions((prompt,)))
    await asyncio.sleep(0)
    assert terminal._picker_kind == "question"
    assert terminal._question_title == "Which action?"
    terminal._picker.current_value = "Apply"
    terminal._pick()
    assert await pending == [["Apply"]]

    pending = asyncio.create_task(terminal._ask_questions((prompt,)))
    await asyncio.sleep(0)
    terminal._picker.current_value = "__free__"
    terminal._pick()
    assert terminal._question_text_active
    terminal._question_text.text = "Make a backup first"
    terminal._answer_question_text()
    assert await pending == [["Make a backup first"]]

    async def answer(questions: tuple[Question, ...]) -> list[list[str]]:
        assert len(questions) == 1
        return [["Inspect"]]

    result = await QuestionTool(answer).execute(
        ToolCallBlock(
            id="ask",
            name="question",
            args={
                "questions": [
                    {"question": "Which action?", "options": [{"label": "Inspect"}]}
                ]
            },
        )
    )
    assert not result.is_error
    assert "Inspect" in result.content


@pytest.mark.asyncio
async def test_question_multiselect_and_skipping(tmp_path: Path) -> None:
    from cade_mini.hosts.interaction_question import Question

    terminal = CadeTui(service(tmp_path))
    prompt = Question(
        "Pick components",
        options=(("TUI", ""), ("Core", ""), ("Tests", "")),
        multiple=True,
    )
    task = asyncio.create_task(terminal._ask_questions((prompt,)))
    await asyncio.sleep(0)
    terminal._picker.current_value = "Core"
    terminal._pick()
    assert "Core" in terminal._question_selected
    terminal._picker.current_value = "Tests"
    terminal._pick()
    terminal._picker.current_value = "__done__"
    terminal._pick()
    assert await task == [["Core", "Tests"]]

    free_task = asyncio.create_task(
        terminal._ask_questions((Question("Additional notes?"),))
    )
    await asyncio.sleep(0)
    assert terminal._question_text_active
    terminal._question_text.text = "No additional notes"
    terminal._answer_question_text()
    assert await free_task == [["No additional notes"]]

    skipped = asyncio.create_task(terminal._ask_questions((prompt,)))
    await asyncio.sleep(0)
    terminal._close_picker()
    assert await skipped == [[]]


@pytest.mark.asyncio
async def test_manual_tool_invocation_emits_foldable_events_and_runs_hooks(
    tmp_path: Path,
) -> None:
    from cade_mini.core.loop import ToolResult

    calls: list[str] = []

    class ReadTool:
        name = "read"
        description = "Read a file"

        def __init__(self) -> None:
            self.parameters = {"type": "object"}

        async def execute(self, call: ToolCallBlock, signal=None) -> ToolResult:
            calls.append("execute")
            return ToolResult(content="sample output")

    class Hooks:
        async def before(self, call: ToolCallBlock) -> tuple[bool, str]:
            calls.append("before")
            return True, ""

        async def after(
            self, call: ToolCallBlock, content: str, is_error: bool
        ) -> None:
            calls.append("after")

    terminal = CadeTui(service(tmp_path))
    terminal.service.base_tools = (ReadTool(),)
    terminal.service.hooks = Hooks()
    result = await terminal.service.execute_tool(
        "read", {"path": "example.py"}, _allow, on_event=terminal._event
    )
    assert result.content == "sample output"
    assert calls == ["before", "execute", "after"]
    rendered = "\n".join(terminal.transcript.ansi_lines(100))
    assert "✓ read" in rendered
    assert "sample output" not in rendered
    terminal.transcript.toggle_tools()
    assert "sample output" in "\n".join(terminal.transcript.ansi_lines(100))


def test_command_palette_is_searchable_and_edits_command_input(
    tmp_path: Path,
) -> None:
    terminal = CadeTui(service(tmp_path))
    terminal._open_picker("commands")
    assert len(terminal._picker_source) > 10
    terminal._picker_search.text = "context"
    assert any(name == "/context" for name, _ in terminal._picker.values)
    terminal._picker.current_value = "/context"
    terminal._pick()
    assert terminal._picker_kind is None
    assert terminal._input.text == "/context"


def test_running_status_shows_elapsed_time(tmp_path: Path) -> None:
    import time

    terminal = CadeTui(service(tmp_path))
    terminal._working_since = time.monotonic() - 3
    status = str(terminal._status_text())
    assert "Running" in status
    assert "3s" in status
    terminal._working_since = None
    assert "Ready" in str(terminal._status_text())


def test_configuration_picker_prefills_existing_validated_value(
    tmp_path: Path,
) -> None:
    host = service(tmp_path)
    host.settings.save("context_window", "250000")
    terminal = CadeTui(host)
    terminal._open_picker("config")
    terminal._picker.current_value = "context_window"
    terminal._pick()
    assert terminal._config_form_active
    assert terminal._config_field == "context_window"
    assert terminal._config_input.text == "250000"
    assert terminal._picker_kind is None
    terminal._config_input.text = "180000"
    terminal._save_config_form()
    assert host.settings._load().context_window == 180000
    assert terminal._picker_kind == "config"


@pytest.mark.asyncio
async def test_live_palette_shortcut_filters_and_selects_command(
    tmp_path: Path,
) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        terminal = CadeTui(service(tmp_path), input=pipe, output=DummyOutput())
        running = asyncio.create_task(terminal.app.run_async())
        try:
            await asyncio.sleep(0.08)
            pipe.send_bytes(b"\x0b")  # Ctrl+K
            await asyncio.sleep(0.1)
            assert terminal._picker_kind == "commands"
            pipe.send_text("context")
            await asyncio.sleep(0.1)
            assert terminal._picker.current_value == "/context"
            pipe.send_bytes(b"\r")
            await asyncio.sleep(0.1)
            assert terminal._picker_kind is None
            assert terminal._input.text == "/context"
            pipe.send_bytes(b"\x11")  # Ctrl+Q
            await asyncio.wait_for(running, timeout=4)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_live_question_tool_waits_for_terminal_answer(
    tmp_path: Path,
) -> None:
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        terminal = CadeTui(service(tmp_path), input=pipe, output=DummyOutput())
        running = asyncio.create_task(terminal.app.run_async())
        try:
            await asyncio.sleep(0.08)
            question_tool = terminal.service.question_tool
            assert question_tool is not None
            question = ToolCallBlock(
                id="question-live",
                name="question",
                args={
                    "questions": [
                        {
                            "question": "Continue?",
                            "options": [{"label": "Proceed"}, {"label": "Cancel"}],
                        }
                    ]
                },
            )
            asked = asyncio.create_task(question_tool.execute(question))
            await asyncio.sleep(0.1)
            assert terminal._picker_kind == "question"
            pipe.send_bytes(b"\r")
            result = await asyncio.wait_for(asked, timeout=4)
            assert "Proceed" in result.content
            assert terminal._picker_kind is None
            pipe.send_bytes(b"\x11")  # Ctrl+Q
            await asyncio.wait_for(running, timeout=4)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)


def test_model_switch_clamps_inherited_unsupported_reasoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = service(tmp_path)
    host.provider_config = ProviderConfig(
        provider="openai",
        model="gpt-6-luna",
        reasoning_effort="none",
    )
    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: FakeProvider(),
    )
    host.select_model("openai/gpt-6-astra")
    assert host.provider_config.reasoning_effort == "high"

    with pytest.raises(ValueError, match="supports reasoning"):
        host.select_model("openai/gpt-6-astra", effort="none")


@pytest.mark.asyncio
async def test_long_question_is_readable_and_free_text_can_be_multiline(
    tmp_path: Path,
) -> None:
    from cade_mini.hosts.interaction_question import Question

    terminal = CadeTui(service(tmp_path))
    long_prompt = "Describe the desired change, including risks and constraints. " * 6
    pending = asyncio.create_task(terminal._ask_questions((Question(long_prompt),)))
    await asyncio.sleep(0)
    assert terminal._question_title_height() > 1
    assert terminal._question_text_active
    terminal._question_text.text = "First requirement\nSecond requirement"
    terminal._answer_question_text()
    assert await pending == [["First requirement\nSecond requirement"]]


@pytest.mark.asyncio
async def test_disconnected_workspace_starts_tui_and_recovers_after_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade_mini.hosts.interaction import UnavailableProvider

    host = service(tmp_path)
    host.provider = UnavailableProvider(
        "gpt-6-luna",
        "openai",
        "Missing API key; run /login",
    )
    terminal = CadeTui(host)
    assert "Sign in required" in str(terminal._status_text())
    assert any("/login" in item.text for item in terminal.transcript.entries)
    before = len(host.log.branch())
    with pytest.raises(RuntimeError, match="Missing API key"):
        await host.run_task("hello", lambda _event: None, _allow)
    assert len(host.log.branch()) == before

    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: FakeProvider(),
    )
    host.select_model("openai/gpt-6-luna")
    assert not isinstance(host.provider, UnavailableProvider)
    assert "Sign in required" not in str(terminal._status_text())


def test_model_change_preserves_compaction_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = service(tmp_path)
    host.compactor = Compactor(host.provider, extra_context=lambda: "WORKING NOTE")
    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: FakeProvider(),
    )
    host.select_model("openai/gpt-6-luna")
    assert host.compactor.extra_context is not None
    assert host.compactor.extra_context() == "WORKING NOTE"


def test_question_parser_accepts_nullable_optional_fields_from_strict_schema() -> None:
    from cade_mini.hosts.interaction_question import parse_questions

    questions = parse_questions(
        [
            {
                "question": "Proceed?",
                "header": None,
                "multiple": None,
                "options": [
                    {"label": "Yes", "description": None},
                    {"label": "No", "description": None},
                ],
            },
            {
                "question": "Why?",
                "header": None,
                "multiple": None,
                "options": None,
            },
        ]
    )
    assert questions[0].options == (("Yes", ""), ("No", ""))
    assert questions[0].multiple is False
    assert questions[1].options == ()


def test_explicit_config_path_is_preserved_during_tui_edits(
    tmp_path: Path,
) -> None:
    from cade_mini.hosts.interaction_settings import SettingsService

    custom = tmp_path / "alternate.json"
    custom.write_text('{"mode":"build"}', encoding="utf-8")
    settings = SettingsService(tmp_path, Path("alternate.json"))
    assert settings.edit_command("mode") == '/config mode="build"'
    settings.save("mode", "act")
    assert '"mode": "act"' in custom.read_text(encoding="utf-8")
    assert not (tmp_path / "cade-mini.json").exists()


def test_explicit_context_window_survives_model_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = service(tmp_path)
    host.budget = Budget(context_window=150_000)
    host.context_window_explicit = True

    class LargerProvider(FakeProvider):
        model = "gpt-6-luna"
        context_window = 300_000

    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: LargerProvider(),
    )
    host.select_model("openai/gpt-6-luna")
    assert host.budget.context_window == 150_000


def test_explicit_context_window_rejects_model_that_cannot_support_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = service(tmp_path)
    host.budget = Budget(context_window=400_000)
    host.context_window_explicit = True

    class SmallProvider(FakeProvider):
        model = "gpt-6-luna"
        context_window = 100_000

    monkeypatch.setattr(
        "cade_mini.hosts.interaction.build_provider",
        lambda _config: SmallProvider(),
    )
    previous = host.provider
    with pytest.raises(ValueError, match="exceeds"):
        host.select_model("openai/gpt-6-luna")
    assert host.provider is previous
    assert host.budget.context_window == 400_000


@pytest.mark.asyncio
async def test_manual_context_window_updates_subagent_budget(
    tmp_path: Path,
) -> None:
    from cade_mini.extensions.subagents import SubagentRunner, SubagentTool

    host = service(tmp_path)
    runner = SubagentRunner(
        workspace=tmp_path,
        provider=host.provider,
        tools=(),
        budget=host.budget,
        sessions_dir=host.sessions_dir,
        system_prompt=host.system_prompt,
        policy=host.policy,
        approve=_allow,
    )
    host.base_tools = (SubagentTool(runner=runner),)
    await host.command("/context-window 150k")
    assert runner.budget is host.budget
    assert runner.budget.context_window == 150_000
    assert host.context_window_explicit


@pytest.mark.asyncio
async def test_session_change_confirms_after_replaying_old_log(
    tmp_path: Path,
) -> None:
    terminal = CadeTui(service(tmp_path))
    await terminal._dispatch("/new")
    assert any(
        entry.text == f"Session: {terminal.service.log.session_id}"
        for entry in terminal.transcript.entries
    )


def test_terminal_mode_is_restored_even_when_tui_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cade_mini.hosts.tui import platform

    restored: list[tuple[int, int]] = []

    class Kernel:
        def SetConsoleMode(self, handle: int, mode: int) -> None:
            restored.append((handle, mode))

    monkeypatch.setattr(platform, "_windows_input_mode", lambda: (Kernel(), 42, 0x0007))
    with (
        pytest.raises(RuntimeError, match="render failure"),
        platform.preserve_console_input_mode(),
    ):
        raise RuntimeError("render failure")
    assert restored == [(42, 0x0007)]


@pytest.mark.asyncio
async def test_shift_tab_only_updates_status_without_transcript(tmp_path: Path) -> None:
    host = service(tmp_path)
    terminal = CadeTui(host)
    terminal._input.text = "Unsent draft"
    before = tuple(terminal.transcript.entries)

    await terminal._cycle_mode()
    assert host.policy.mode == "build"
    assert tuple(terminal.transcript.entries) == before
    assert terminal._input.text == "Unsent draft"
    assert "build" in str(terminal._status_text())

    await terminal._cycle_mode()
    assert host.policy.mode == "plan"
    assert tuple(terminal.transcript.entries) == before


def test_model_picker_filters_credentials_and_adjusts_effort_per_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade_mini.app.auth import JsonCredentialStore

    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    host = service(tmp_path)
    host.auth.store = JsonCredentialStore(tmp_path / "private-auth.json")
    terminal = CadeTui(host)
    terminal._open_picker("model")

    assert all(
        not name.startswith("chatgpt/") for name, _label in terminal._picker_source
    )
    target = "openai/gpt-6-luna"
    index = next(
        i for i, (name, _label) in enumerate(terminal._picker.values) if name == target
    )
    terminal._picker._selected_index = index
    terminal._picker.current_value = target
    assert terminal._model_efforts[target] == "medium"

    terminal._adjust_picker_effort(1)
    assert terminal._model_efforts[target] == "high"
    assert terminal._picker.current_value == target
    label = next(label for name, label in terminal._picker.values if name == target)
    assert label.count("gpt-6-luna") == 1
    assert "‹ high ›" in label
    assert terminal._hint_height() == 0


def test_nonempty_resume_and_single_label_for_mode_and_tool(
    tmp_path: Path,
) -> None:
    host = service(tmp_path)
    empty = SessionLog.create(host.sessions_dir, session_id="empty")
    useful = SessionLog.create(host.sessions_dir, session_id="useful")
    useful.append("user", {"text": "My actual work"})
    assert empty.session_id not in dict(host.choices("sessions"))
    assert useful.session_id in dict(host.choices("sessions"))

    class ReadTool:
        name = "read"
        description = "Read a workspace file"

        def __init__(self) -> None:
            self.parameters = {"type": "object"}

        async def execute(self, call: ToolCallBlock, signal=None):
            from cade_mini.core.loop import ToolResult

            return ToolResult(content="ok")

    host.base_tools = (ReadTool(),)
    names = dict(host.choices("tool"))
    assert names["read"].count("read") == 1
    assert "Read a workspace file" in names["read"]
    assert dict(host.choices("mode"))["act"] == "act (current)"

    terminal = CadeTui(host)
    terminal._open_picker("mode")
    assert terminal._hint_height() == 0
    assert terminal._picker_hint_text().count("Esc cancel") == 1


@pytest.mark.asyncio
async def test_context_report_uses_budget_categories_and_no_raw_dict(
    tmp_path: Path,
) -> None:
    host = service(tmp_path)
    host.log.append("user", {"text": "Inspect the code"})
    host.last_usage = {"input": 2263, "output": 15}
    text = "\n".join((await host.command("/context")).lines)
    assert "Context Usage · test-model" in text
    assert "Local estimates:" in text
    for name in (
        "Fixed prefix",
        "Durable state",
        "Working messages",
        "Tool evidence",
        "Input budget:",
        "Remaining input:",
        "Output reserve:",
        "Headroom:",
    ):
        assert name in text
    assert "Last run · input" in text
    assert "{'input'" not in text


def test_config_menu_uses_labels_and_provider_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = service(tmp_path)
    monkeypatch.setattr(
        host.settings, "configured_providers", lambda *_args: ("openai",)
    )
    labels = dict(host.choices("config"))
    assert "Default Provider" in labels["provider"]
    assert "Default Model" in labels["model"]

    terminal = CadeTui(host)
    terminal._open_picker("config")
    terminal._picker.current_value = "provider"
    terminal._pick()
    assert terminal._picker_kind == "config_value"
    assert len(terminal._picker.values) == 1
    assert terminal._picker.values[0][0] == '"openai"'
    terminal._pick()
    assert terminal._picker_kind == "config"
    assert host.settings._load().provider == "openai"
