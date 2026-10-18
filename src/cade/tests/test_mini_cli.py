"""Cade Mini CLI 的回归测试。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.mini.cli import main
from cade.mini.log import SessionLog


class FakeProvider:
    model = "fake"
    transport = "fake"

    def __init__(self, batches: list[list[ProviderEvent]]) -> None:
        self.batches = list(batches)
        self.requests: list[
            tuple[list[dict[str, Any]], list[ToolDefinition], StreamOptions | None]
        ] = []

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append((messages, tools, options))
        batch = self.batches.pop(0)

        async def events() -> AsyncIterator[ProviderEvent]:
            for event in batch:
                yield event

        return events()


def _text(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


def _session_files(sessions: Path) -> list[Path]:
    return sorted(sessions.glob("session-*.jsonl"))


def test_cli_exec_prints_answer_and_creates_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sessions = tmp_path / "sessions"
    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(sessions),
            "--yes",
        ],
        provider=FakeProvider([_text("hello")]),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "hello" in captured.out
    paths = _session_files(sessions)
    assert len(paths) == 1
    log = SessionLog.open(paths[0])
    types = [entry.type for entry in log.branch()]
    assert types[0] == "meta"
    assert "system" in types
    assert types.count("user") == 1
    assert types[-1] == "run"


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--yes",
            "--json",
        ],
        provider=FakeProvider([_text("hello")]),
    )

    captured = capsys.readouterr()
    assert code == 0
    payloads = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert any(payload["type"] == "text" for payload in payloads)
    assert payloads[-1] == {
        "type": "final",
        "termination": "completed",
        "answer": "hello",
        "error": None,
        "usage": None,
    }


def test_cli_resume_appends_to_same_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    common = [
        "--project-root",
        str(tmp_path),
        "--sessions-dir",
        str(sessions),
        "--yes",
    ]
    assert (
        main(
            ["exec", "first", *common],
            provider=FakeProvider([_text("one")]),
        )
        == 0
    )
    session_id = _session_files(sessions)[0].stem.removeprefix("session-")
    assert (
        main(
            ["exec", "second", *common, "--resume", session_id],
            provider=FakeProvider([_text("two")]),
        )
        == 0
    )

    paths = _session_files(sessions)
    assert len(paths) == 1
    users = [
        entry for entry in SessionLog.open(paths[0]).branch() if entry.type == "user"
    ]
    assert [str(entry.data["text"]) for entry in users] == ["first", "second"]


def test_cli_repl_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": "/exit")
    code = main(
        [
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
        ],
        provider=FakeProvider([]),
    )

    assert code == 0
    assert "Cade Mini session" in capsys.readouterr().out


def test_cli_read_only_denies_write(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sessions = tmp_path / "sessions"
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="w1",
                            name="write",
                            input={"path": "a.txt", "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )

    code = main(
        [
            "exec",
            "write",
            "--project-root",
            str(workspace),
            "--sessions-dir",
            str(sessions),
            "--read-only",
        ],
        provider=provider,
    )

    assert code == 0
    assert not (workspace / "a.txt").exists()
    log = SessionLog.open(_session_files(sessions)[0])
    tool_results = [entry for entry in log.branch() if entry.type == "tool_result"]
    assert tool_results[0].data["status"] == "error"


def test_cli_rejects_json_in_repl(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--json",
        ],
        provider=FakeProvider([]),
    )

    assert code == 2
    assert "--json requires" in capsys.readouterr().err
