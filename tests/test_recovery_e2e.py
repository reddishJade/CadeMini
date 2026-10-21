"""通过真实键盘输入验证检查点、恢复范围和逐步重做。"""

from __future__ import annotations

import json
import threading
import time

import pytest
from test_tui_e2e import Runner, Terminal, terminal_runner

from cade.ai.events import FinalMessage, ToolCall, ToolCallEvent
from cade.coding.workspace.snapshots import FileState

__all__ = ["terminal_runner"]
pytestmark = pytest.mark.e2e


async def _write(term: Terminal, prompt: str, files: dict[str, str]) -> None:
    prefix = str(len(term.service.provider.requests))
    term.service.provider.batches.extend(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id=f"c-{prefix}-{i}",
                            name="write",
                            input={"path": path, "content": content},
                        )
                        for i, (path, content) in enumerate(files.items())
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            [FinalMessage("written", "end_turn")],
        ]
    )
    await term.send(prompt)
    for path, content in files.items():
        assert (term.service.workspace / path).read_text(encoding="utf-8") == content


def _users(term: Terminal) -> list[str]:
    return [str(e.data["text"]) for e in term.service.log.branch() if e.type == "user"]


def test_stepwise_undo_redo_survives_tui_restart(terminal_runner: Runner) -> None:
    saved: dict[str, str] = {}

    async def before_restart(term: Terminal) -> None:
        await _write(term, "first write", {"a.txt": "one"})
        await _write(term, "second write", {"a.txt": "two"})
        saved["session"] = term.service.log.session_id
        requests = len(term.service.provider.requests)
        await term.send("/undo")
        assert (term.service.workspace / "a.txt").read_text() == "one"
        assert _users(term) == ["first write"]
        assert term.tui._input.text == "second write"
        assert len(term.service.provider.requests) == requests

    terminal_runner(before_restart)

    async def after_restart(term: Terminal) -> None:
        assert term.tui._input.text == "second write"
        requests = len(term.service.provider.requests)
        await term.send("/undo")
        assert not (term.service.workspace / "a.txt").exists()
        assert _users(term) == []
        await term.send("/redo")
        assert (term.service.workspace / "a.txt").read_text() == "one"
        assert _users(term) == ["first write"]
        await term.send("/redo")
        assert (term.service.workspace / "a.txt").read_text() == "two"
        assert _users(term) == ["first write", "second write"]
        assert len(term.service.provider.requests) == requests

    terminal_runner(after_restart, resume=saved["session"])


@pytest.mark.parametrize("scope", ["conversation", "code", "both"])
def test_native_rewind_scope_selection(terminal_runner: Runner, scope: str) -> None:
    async def scenario(term: Terminal) -> None:
        await _write(term, "first write", {"a.txt": "one"})
        await _write(term, "second write", {"a.txt": "two"})
        requests = len(term.service.provider.requests)
        await term.send("/rewind")
        await term.choose(0)
        picker = term.tui._state.pending_command_choice
        assert picker is not None
        index = next(
            i
            for i, (_, command) in enumerate(picker.choices)
            if str(command).endswith(" " + scope)
        )
        await term.choose(index)
        assert (term.service.workspace / "a.txt").read_text() == "two"
        await term.choose(1)
        assert _users(term) == (
            ["first write", "second write"] if scope == "code" else []
        )
        path = term.service.workspace / "a.txt"
        assert (path.read_text() if path.exists() else None) == (
            "two" if scope == "conversation" else None
        )
        await term.send("/undo")
        assert path.read_text() == "two"
        assert _users(term) == ["first write", "second write"]
        assert len(term.service.provider.requests) == requests

    terminal_runner(scenario)


def test_native_revert_range_and_file_selection(terminal_runner: Runner) -> None:
    async def scenario(term: Terminal) -> None:
        await _write(term, "first write", {"a.txt": "a", "b.txt": "b"})
        await _write(term, "second write", {"c.txt": "c"})
        requests = len(term.service.provider.requests)
        await term.send("/revert")
        await term.choose(0)
        await term.choose(1)
        picker = term.tui._state.pending_command_choice
        assert picker is not None
        file_index = next(
            i for i, (label, _) in enumerate(picker.choices) if label == "a.txt"
        )
        await term.choose(file_index)
        await term.choose(1)
        assert not (term.service.workspace / "a.txt").exists()
        assert (term.service.workspace / "b.txt").read_text() == "b"
        assert (term.service.workspace / "c.txt").read_text() == "c"
        assert _users(term) == ["first write", "second write"]
        await term.send("/undo")
        assert (term.service.workspace / "a.txt").read_text() == "a"
        assert len(term.service.provider.requests) == requests
        await term.send("inspect after restore")
        assert "workspace-restore:" in json.dumps(term.service.provider.requests[-1])

    terminal_runner(scenario)


def test_double_escape_opens_rewind_and_cancel_changes_nothing(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await _write(term, "write", {"a.txt": "a"})
        original = term.service.log.path.read_bytes()
        term.pipe.send_text("\x1b\x1b")
        await term.until(
            lambda: term.tui._state.pending_command_choice is not None, "rewind menu"
        )
        await term.choose(0)
        await term.choose(0)
        await term.escape()
        assert term.service.log.path.read_bytes() == original
        assert (term.service.workspace / "a.txt").read_text() == "a"

    terminal_runner(scenario)


def test_parent_turn_undo_includes_synchronous_subagent_edits(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        term.service.provider.batches.extend(
            [
                [
                    ToolCallEvent(
                        [
                            ToolCall(
                                id="delegate-call",
                                name="subagent",
                                input={"prompt": "Write child.txt"},
                            )
                        ]
                    ),
                    FinalMessage("", "tool_use"),
                ],
                [
                    ToolCallEvent(
                        [
                            ToolCall(
                                id="child-write",
                                name="write",
                                input={"path": "child.txt", "content": "child"},
                            )
                        ]
                    ),
                    FinalMessage("", "tool_use"),
                ],
                [FinalMessage("child done", "end_turn")],
                [FinalMessage("parent done", "end_turn")],
            ]
        )
        await term.send("delegate a write")
        assert (term.service.workspace / "child.txt").read_text() == "child"
        assert len(term.service.provider.requests) == 4
        children = list(
            (term.service.sessions_dir / "subagents").glob("session-*.jsonl")
        )
        assert len(children) == 1
        await term.send("/undo")
        assert not (term.service.workspace / "child.txt").exists()
        assert _users(term) == []
        await term.send("/redo")
        assert (term.service.workspace / "child.txt").read_text() == "child"
        assert len(term.service.provider.requests) == 4

    terminal_runner(scenario)


def test_shell_rename_is_reversible_without_touching_unrelated_files(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        root = term.service.workspace
        (root / "old name.txt").write_text("original", encoding="utf-8")
        (root / "unrelated.txt").write_text("before", encoding="utf-8")
        term.service.provider.batches.extend(
            [
                [
                    ToolCallEvent(
                        [
                            ToolCall(
                                id="rename",
                                name="bash",
                                input={"command": 'mv "old name.txt" "new name.txt"'},
                            )
                        ]
                    ),
                    FinalMessage("", "tool_use"),
                ],
                [FinalMessage("renamed", "end_turn")],
            ]
        )
        await term.send("rename the file")
        assert not (root / "old name.txt").exists()
        assert (root / "new name.txt").read_text() == "original"
        (root / "unrelated.txt").write_text("human", encoding="utf-8")
        await term.send("/undo")
        assert (root / "old name.txt").read_text() == "original"
        assert not (root / "new name.txt").exists()
        await term.send("/redo")
        assert not (root / "old name.txt").exists()
        assert (root / "new name.txt").read_text() == "original"
        assert (root / "unrelated.txt").read_text() == "human"
        assert len(term.service.provider.requests) == 2

    terminal_runner(scenario)


def test_exit_waits_for_recovery_commit(
    terminal_runner: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(term: Terminal) -> None:
        await _write(term, "write", {"a.txt": "one"})
        started = threading.Event()
        original = term.service.snapshots.apply_state

        def slow(
            path: str, expected: FileState | None, desired: FileState | None
        ) -> None:
            started.set()
            time.sleep(0.5)
            original(path, expected, desired)

        monkeypatch.setattr(term.service.snapshots, "apply_state", slow)
        await term.send("/undo", wait_idle=False)
        await term.until(started.is_set, "recovery started")
        term.pipe.send_text("\x1b")
        await term.until(
            lambda: "Recovery is committing" in term.transcript,
            "non-cancellable commit",
        )
        term.pipe.send_text("\x03\x03")
        await term.until(
            lambda: not term.tui._application.is_running, "commit before exit"
        )
        assert not (term.service.workspace / "a.txt").exists()
        assert not term.service.recovery.store.pending_path.exists()
        assert len(term.service.provider.requests) == 2

    terminal_runner(scenario)


def test_conflicting_manual_edit_blocks_all_files_in_tui(
    terminal_runner: Runner,
) -> None:
    async def scenario(term: Terminal) -> None:
        await _write(term, "write", {"a.txt": "a", "b.txt": "b"})
        (term.service.workspace / "a.txt").write_text("human", encoding="utf-8")
        await term.send("/undo")
        assert "changed since checkpoint" in term.transcript
        assert (term.service.workspace / "a.txt").read_text() == "human"
        assert (term.service.workspace / "b.txt").read_text() == "b"
        assert _users(term) == ["write"]

    terminal_runner(scenario)
