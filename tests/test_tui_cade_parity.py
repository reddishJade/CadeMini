"""Behavioral parity checks for Cade Mini's terminal chrome and slash commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.output import DummyOutput

from cade_mini.ai.events import FinalMessage
from cade_mini.app.provider import ProviderConfig
from cade_mini.core.compaction import Compactor
from cade_mini.core.log import SessionLog
from cade_mini.core.request import Budget
from cade_mini.extensions.goal import GoalStore
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.hosts.command_catalog import COMMAND_INDEX, COMMANDS, command_help
from cade_mini.hosts.interaction import InteractionService
from cade_mini.hosts.tui.app import CadeTui
from cade_mini.hosts.tui.chrome import fit_text, welcome_ansi_lines, welcome_text
from cade_mini.hosts.tui.completion import CadeCompleter, CommandArgsSuggester
from cade_mini.hosts.tui.widgets import TuiInputLexer
from cade_mini.safety.policy import WorkspacePolicy


class StaticProvider:
    model = "test-model"
    transport = "openai"
    context_window = 200_000

    async def stream(self, messages, tools, options=None):
        yield FinalMessage(content="ok", stop_reason="end_turn")


def host(tmp_path: Path) -> InteractionService:
    sessions = tmp_path / ".cade-mini" / "sessions"
    log = SessionLog.create(sessions)
    log.append("system", {"text": "Assistant."})
    provider = StaticProvider()
    return InteractionService(
        log=log,
        sessions_dir=sessions,
        system_prompt="Assistant.",
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


def test_all_cade_slash_command_names_are_supported() -> None:
    expected = {
        "/help",
        "/clear",
        "/continue",
        "/new",
        "/fork",
        "/clone",
        "/rewind",
        "/resume",
        "/sessions",
        "/tree",
        "/model",
        "/effort",
        "/context-window",
        "/thinking",
        "/login",
        "/connect",
        "/logout",
        "/auth",
        "/config",
        "/mode",
        "/steer",
        "/queue",
        "/compact",
        "/rollover",
        "/goal",
        "/permissions",
        "/hooks",
        "/mcp",
        "/tool",
        "/skill",
        "/memory",
        "/rename",
        "/undo",
        "/exit",
        "/context",
        "/btw",
        "/quit",
        "/revert",
        "/new-context",
    }
    assert expected <= set(COMMAND_INDEX)
    assert all(COMMAND_INDEX[name].canonical is None for name in COMMANDS)
    assert COMMAND_INDEX["/connect"].canonical == "/login"
    assert COMMAND_INDEX["/revert"].canonical == "/undo"
    assert any(line == "Sessions" for line in command_help())


def test_command_completion_and_argument_hints(tmp_path: Path) -> None:
    service = host(tmp_path)
    completer = CadeCompleter(service)
    commands = [
        completion.text
        for completion in completer.get_completions(Document("/mod"), object())
    ]
    assert "/model" in commands
    assert "/mode" in commands

    choices = {
        item.text for item in completer.get_completions(Document("/auth lo"), object())
    }
    assert "login" in choices
    assert "logout" in choices

    choices = {
        item.text
        for item in completer.get_completions(Document("/config mod"), object())
    }
    assert "mode" in choices

    suggester = CommandArgsSuggester()
    suggestion = suggester.get_suggestion(Buffer(), Document("/model"))
    assert suggestion is not None and "provider" in suggestion.text
    assert suggester.get_suggestion(Buffer(), Document("/exit")) is None


def test_welcome_matches_cade_layout_and_narrow_terminal() -> None:
    expanded = welcome_text(
        tools=("read", "write", "bash"),
        instructions=(Path("/workspace/AGENTS.md"),),
        skills=("review",),
        width=100,
    )
    assert "█▀▀▀" in expanded
    assert "Describe a task to get started" in expanded
    assert "[Tools]" in expanded
    assert "[Context]" in expanded
    assert "[Skills]" in expanded
    assert "Cade-level" not in expanded
    assert "\x1b[" in "\n".join(welcome_ansi_lines(expanded))
    short = welcome_text(
        tools=("read",),
        instructions=(),
        skills=(),
        width=35,
        compact=True,
    )
    assert "cade mini v" in short
    assert "█▀▀▀" not in short
    assert fit_text("你好世界", 5) == "你好…"


def test_file_references_are_highlighted_in_terminal_editor() -> None:
    highlights = TuiInputLexer().lex_document(
        Document("Review @src/main.py and @README.md")
    )(0)
    assert any(
        style == "fg:ansicyan bold" and "@src/main.py" in text
        for style, text in highlights
    )
    assert any("@README.md" in text for style, text in highlights)


@pytest.mark.asyncio
async def test_command_handlers_open_native_actions(tmp_path: Path) -> None:
    service = host(tmp_path)
    user = service.log.append("user", {"text": "A change request"})
    assert (await service.command("/fork")).picker == "fork"
    assert any(item == user.id for item, _ in service.choices("fork"))
    assert (await service.command("/connect openai")).login_provider == "openai"
    assert (await service.command("/auth login chatgpt")).login_provider == "chatgpt"
    assert (await service.command("/auth status")).lines
    assert (await service.command("/tool list")).lines
    assert (await service.command("/undo --list")).lines == ("No snapshots available",)
    setting = await service.command("/config mode")
    assert setting.config_field == "mode"
    assert service.settings.edit_command(setting.config_field).startswith(
        "/config mode="
    )

    await service.command("/goal Implement a prompt")
    assert "paused:" in (await service.command("/goal pause")).lines[0]
    assert "active:" in (await service.command("/goal resume")).lines[0]
    assert "done:" in (await service.command("/goal done")).lines[0]
    assert "Goal cleared" in (await service.command("/goal clear")).lines
    no_note = await service.command("/rollover")
    assert "NOTE.md" in no_note.lines[0]


def test_tui_has_compact_editor_and_contextual_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    terminal = CadeTui(host(tmp_path), output=DummyOutput())
    assert terminal.app.full_screen is False
    assert terminal._input_height() == 1
    assert terminal._hint_height() == 0
    terminal._input.text = "two lines\nfor editing"
    assert terminal._input_height() == 2
    terminal._input.text = ""
    terminal._show_shortcuts()
    assert any("Ctrl+K" in item.text for item in terminal.transcript.entries)
    terminal._open_picker("model")
    assert terminal._picker.current_value == "openai/test-model"
    model_labels = dict(terminal._picker_source)
    assert model_labels["__custom__"] == "Enter a custom model name…"
    assert "openai/test-model" in model_labels
    assert model_labels["openai/test-model"].count("test-model") == 1
    terminal._close_picker()

    terminal._open_picker("mode")
    labels = dict(terminal._picker_source)
    assert labels["act"].startswith("act")
    assert labels["build"].startswith("build")
    assert labels["plan"].startswith("plan")
    terminal._close_picker()


def test_argument_completions_do_not_repeat_candidate_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")
    service = host(tmp_path)
    completer = CadeCompleter(service)
    mode = list(completer.get_completions(Document("/mode "), object()))
    assert {item.text for item in mode} >= {"act", "build", "plan"}
    assert all(item.display_meta_text != item.text for item in mode)

    tool = list(
        completer._matches(
            "", (("read", "read · Read a text file"), ("edit", "edit · Replace text")),
            0,
        )
    )
    assert [item.display_meta_text for item in tool] == [
        "· Read a text file",
        "· Replace text",
    ]

    model = list(completer.get_completions(Document("/model "), object()))
    assert model
    assert all(not item.display_meta_text.startswith(item.text) for item in model)
