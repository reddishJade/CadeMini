"""通过真实 TUI 输入验证会话树、独立副本和重新启动后的恢复。"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from test_tui_e2e import Runner, Terminal, terminal_runner

import cade.harness.session.log as session_log
from cade.harness.session.log import SessionLog, list_sessions

__all__ = ["terminal_runner"]

pytestmark = pytest.mark.e2e


def _users(log: SessionLog) -> list[str]:
    return [str(e.data["text"]) for e in log.branch() if e.type == "user"]


def test_tree_restarts_at_selected_input_and_preserves_alternatives(
    terminal_runner: Runner,
) -> None:
    saved: dict[str, str] = {}

    async def before_restart(term: Terminal) -> None:
        await term.send("E2E ROOT PROMPT")
        await term.send("E2E ORIGINAL SECOND")
        log = term.service.log
        users = [e for e in log.branch() if e.type == "user"]
        assistants = [e for e in log.branch() if e.type == "assistant"]
        original = log.path.read_bytes()
        saved.update(
            session=log.session_id, user=users[-1].id, assistant=assistants[-1].id
        )
        (term.service.workspace / "marker.txt").write_text(
            "unchanged", encoding="utf-8"
        )
        await term.send("/tree " + saved["user"])
        assert term.service.log.session_id == saved["session"]
        assert _users(term.service.log) == ["E2E ROOT PROMPT"]
        assert term.tui._input.text == "E2E ORIGINAL SECOND"
        assert log.path.read_bytes().startswith(original)
        assert len(term.service.provider.requests) == 2
        assert len(list(term.service.sessions_dir.glob("session-*.jsonl"))) == 1

    terminal_runner(before_restart)

    async def after_restart(term: Terminal) -> None:
        assert term.service.log.session_id == saved["session"]
        assert term.tui._input.text == "E2E ORIGINAL SECOND"
        assert _users(term.service.log) == ["E2E ROOT PROMPT"]
        assert [info.id for info in list_sessions(term.service.sessions_dir)] == [
            saved["session"]
        ]
        await term.send("E2E ALTERNATIVE SECOND")
        request = json.dumps(term.service.provider.requests[-1])
        assert "E2E ROOT PROMPT" in request and "E2E ALTERNATIVE SECOND" in request
        assert "E2E ORIGINAL SECOND" not in request
        await term.send("/tree")
        picker = term.tui._state.pending_command_choice
        assert picker is not None
        index = next(
            i
            for i, (_, value) in enumerate(picker.choices)
            if value == saved["assistant"]
        )
        await term.choose(index)
        assert term.service.log.session_id == saved["session"]
        assert term.tui._input.text == ""
        assert _users(term.service.log) == ["E2E ROOT PROMPT", "E2E ORIGINAL SECOND"]
        await term.send("E2E CONTINUE ORIGINAL")
        request = json.dumps(term.service.provider.requests[-1])
        assert "E2E ORIGINAL SECOND" in request
        assert "E2E ALTERNATIVE SECOND" not in request
        full_users = [
            e.data["text"] for e in term.service.log.entries() if e.type == "user"
        ]
        assert full_users == [
            "E2E ROOT PROMPT",
            "E2E ORIGINAL SECOND",
            "E2E ALTERNATIVE SECOND",
            "E2E CONTINUE ORIGINAL",
        ]
        assert (term.service.workspace / "marker.txt").read_text(
            encoding="utf-8"
        ) == "unchanged"
        assert len(list(term.service.sessions_dir.glob("session-*.jsonl"))) == 1

    terminal_runner(after_restart, resume=saved["session"])


def test_fork_inactive_input_and_clone_active_path_are_independent(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("E2E ROOT PROMPT")
        await term.send("E2E ORIGINAL SECOND")
        source = term.service.log
        selected = next(
            e for e in source.branch() if e.data.get("text") == "E2E ORIGINAL SECOND"
        )
        await term.send("/tree " + selected.id)
        assert term.tui._input.text == "E2E ORIGINAL SECOND"
        original = source.path.read_bytes()
        await term.send("/clone")
        clone = term.service.log
        assert clone.session_id != source.session_id
        assert _users(clone) == ["E2E ROOT PROMPT"]
        assert term.tui._input.text == ""
        assert not any(e.type == "branch" for e in clone.entries())
        assert source.path.read_bytes() == original
        await term.send("/resume " + source.session_id)
        assert term.tui._input.text == "E2E ORIGINAL SECOND"
        await term.send("E2E ALTERNATIVE SECOND")
        original = source.path.read_bytes()
        await term.send("/fork")
        picker = term.tui._state.pending_command_choice
        assert picker is not None
        index = next(
            i for i, (_, value) in enumerate(picker.choices) if value == selected.id
        )
        await term.choose(index)
        fork = term.service.log
        assert fork.session_id not in {source.session_id, clone.session_id}
        assert term.tui._input.text == "E2E ORIGINAL SECOND"
        assert _users(fork) == ["E2E ROOT PROMPT"]
        assert source.path.read_bytes() == original
        await term.send("E2E FORKED SECOND")
        assert _users(SessionLog.open(fork.path)) == [
            "E2E ROOT PROMPT",
            "E2E FORKED SECOND",
        ]
        assert _users(SessionLog.open(clone.path)) == ["E2E ROOT PROMPT"]
        assert source.path.read_bytes() == original
        request = json.dumps(term.service.provider.requests[-1])
        assert "E2E ORIGINAL SECOND" not in request
        assert "E2E ALTERNATIVE SECOND" not in request
        assert len(list(term.service.sessions_dir.glob("session-*.jsonl"))) == 3

    terminal_runner(scenario)


def test_fork_distinguishes_numeric_entry_ids_from_recent_turn_numbers(
    terminal_runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(term: Terminal) -> None:
        real_uuid4 = session_log.uuid4
        forced = [UUID("12345678901200000000000000000000")]

        def next_uuid() -> UUID:
            return forced.pop() if forced else real_uuid4()

        with monkeypatch.context() as patch:
            patch.setattr(session_log, "uuid4", next_uuid)
            await term.send("numeric ID input")
        first = next(e for e in term.service.log.branch() if e.type == "user")
        assert first.id == "123456789012"
        await term.send("latest input")
        source_id = term.service.log.session_id
        await term.send("/fork 1")
        assert _users(term.service.log) == ["numeric ID input"]
        assert term.tui._input.text == "latest input"
        await term.send("/resume " + source_id)
        await term.send("/fork " + first.id)
        assert term.service.log.session_id != source_id
        assert _users(term.service.log) == []
        assert term.tui._input.text == "numeric ID input"
        assert len(term.service.provider.requests) == 2

    terminal_runner(scenario)


def test_restart_before_first_user_still_offers_resume(terminal_runner: Runner) -> None:
    saved: dict[str, str] = {}

    async def before_restart(term: Terminal) -> None:
        await term.send("E2E FIRST INPUT")
        first = next(e for e in term.service.log.branch() if e.type == "user")
        saved["session"] = term.service.log.session_id
        await term.send("/tree " + first.id)
        assert _users(term.service.log) == []

    terminal_runner(before_restart)

    async def after_restart(term: Terminal) -> None:
        assert _users(term.service.log) == []
        assert term.tui._input.text == "E2E FIRST INPUT"
        await term.send("/sessions")
        picker = term.tui._state.pending_command_choice
        assert picker is not None
        assert [value.id for _, value in picker.choices] == [saved["session"]]
        await term.choose()
        assert term.tui._input.text == "E2E FIRST INPUT"

    terminal_runner(after_restart, resume=saved["session"])


def test_cancel_and_invalid_tree_selection_do_not_change_history(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await term.send("original task")
        log = term.service.log
        original = log.path.read_bytes()
        await term.send("/context")
        await term.send("/tree")
        await term.escape()
        assert log.path.read_bytes() == original
        await term.send("/tree missing-entry")
        assert "entry not found" in term.transcript
        assert "/context" in term.transcript
        assert log.path.read_bytes() == original
        assert term.service.log is log

    terminal_runner(scenario)


@pytest.mark.parametrize(
    "command", ["/tree", "/fork", "/clone", "/undo", "/redo", "/rewind", "/revert"]
)
def test_history_commands_do_not_replace_an_active_run(
    terminal_runner: Runner, command: str
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.delay = 10
        await term.send("running task", wait_idle=False)
        await term.send(command, wait_idle=False)
        assert "after the current task finishes" in term.transcript
        assert term.tui._state.pending_command_choice is None
        assert not any(e.type == "branch" for e in term.service.log.entries())
        assert len(list(term.service.sessions_dir.glob("session-*.jsonl"))) == 1
        await term.send("/stop", wait_idle=False)
        await term.until(lambda: not term.tui._state.running, "run stopped")
        assert len(term.service.provider.requests) == 1

    terminal_runner(scenario)
