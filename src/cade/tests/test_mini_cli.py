"""Cade Mini CLI 的回归测试。"""

from __future__ import annotations

import json
import os
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


def test_cli_includes_project_instructions(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "PROJECT RULE 42: read before edit",
        encoding="utf-8",
    )
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(workspace),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--yes",
        ],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "PROJECT RULE 42" in system_text


def test_cli_no_instructions_flag(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT RULE 42", encoding="utf-8")
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(workspace),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--yes",
            "--no-instructions",
        ],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "PROJECT RULE 42" not in system_text


def test_cli_list_sessions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sessions = tmp_path / "sessions"
    first = SessionLog.create(sessions, session_id="first")
    first.append("user", {"text": "first task", "source": "user"})
    second = SessionLog.create(sessions, session_id="second")
    second.append("user", {"text": "second task", "source": "user"})
    os.utime(first.path, (1, 1))
    os.utime(second.path, (2, 2))

    code = main(
        [
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(sessions),
            "--list-sessions",
        ],
        provider=FakeProvider([]),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "first task" in captured.out
    assert "second task" in captured.out
    assert captured.out.index("second task") < captured.out.index("first task")


def test_cli_continue_uses_latest_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    first = SessionLog.create(sessions, session_id="first")
    first.append("user", {"text": "old task", "source": "user"})
    second = SessionLog.create(sessions, session_id="second")
    second.append("user", {"text": "latest task", "source": "user"})
    os.utime(first.path, (1, 1))
    os.utime(second.path, (2, 2))

    code = main(
        [
            "exec",
            "third",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(sessions),
            "--continue",
            "--yes",
        ],
        provider=FakeProvider([_text("ok")]),
    )

    assert code == 0
    first_users = [
        entry for entry in SessionLog.open(first.path).branch() if entry.type == "user"
    ]
    second_users = [
        entry for entry in SessionLog.open(second.path).branch() if entry.type == "user"
    ]
    assert len(first_users) == 1
    assert len(second_users) == 2


def test_cli_rejects_continue_and_resume(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--continue",
            "--resume",
            "abc",
        ],
        provider=FakeProvider([]),
    )

    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_cli_config_sets_sessions_dir_and_max_steps(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "cade-mini.json").write_text(
        json.dumps({"sessions_dir": "data/sessions", "max_steps": 1}),
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="r1",
                            name="read",
                            input={"path": "missing.txt"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ]
        ]
    )

    code = main(
        ["exec", "hi", "--project-root", str(workspace), "--yes"],
        provider=provider,
    )

    assert code == 1
    files = list((workspace / "data" / "sessions").glob("session-*.jsonl"))
    assert len(files) == 1
    run_entry = SessionLog.open(files[0]).branch()[-1]
    assert run_entry.data["termination"] == "step_limit"


def test_cli_config_can_disable_instructions(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT RULE 42", encoding="utf-8")
    (workspace / "cade-mini.json").write_text(
        json.dumps({"instructions": False}),
        encoding="utf-8",
    )
    provider = FakeProvider([_text("ok")])

    code = main(
        ["exec", "hi", "--project-root", str(workspace), "--yes"],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "PROJECT RULE 42" not in system_text


def test_cli_config_read_only_can_be_overridden(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "cade-mini.json").write_text(
        json.dumps({"read_only": True}),
        encoding="utf-8",
    )

    def write_provider() -> FakeProvider:
        return FakeProvider(
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

    assert (
        main(
            ["exec", "write", "--project-root", str(workspace), "--yes"],
            provider=write_provider(),
        )
        == 0
    )
    assert not (workspace / "a.txt").exists()

    assert (
        main(
            [
                "exec",
                "write",
                "--project-root",
                str(workspace),
                "--yes",
                "--no-read-only",
            ],
            provider=write_provider(),
        )
        == 0
    )
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "x"


def test_cli_invalid_config_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "cade-mini.json").write_text("{bad", encoding="utf-8")

    code = main(
        ["exec", "hi", "--project-root", str(tmp_path)],
        provider=FakeProvider([]),
    )

    assert code == 2
    assert "config" in capsys.readouterr().err


def test_cli_exposes_history_tool(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="h1",
                            name="history",
                            input={"query": "hi"},
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
            "hi",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(sessions),
            "--yes",
        ],
        provider=provider,
    )

    assert code == 0
    log = SessionLog.open(_session_files(sessions)[0])
    tool_result = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert "Matching entries" in str(tool_result.data["content"])
    assert "hi" in str(tool_result.data["content"])


def test_cli_exposes_skill_catalog_and_tool(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    skill_dir = workspace / ".cade" / "skills" / "greeter"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greeter\ndescription: Say hello\n---\nSay HELLO.",
        encoding="utf-8",
    )
    sessions = tmp_path / "sessions"
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [ToolCall(id="s1", name="skill", input={"name": "greeter"})]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )

    code = main(
        [
            "exec",
            "use the skill",
            "--project-root",
            str(workspace),
            "--sessions-dir",
            str(sessions),
            "--yes",
        ],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "<available-skills>" in system_text
    assert "greeter" in system_text
    log = SessionLog.open(_session_files(sessions)[0])
    tool_result = next(entry for entry in log.branch() if entry.type == "tool_result")
    assert "Say HELLO." in str(tool_result.data["content"])
