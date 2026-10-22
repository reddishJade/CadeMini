"""Cade CLI 的回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.coding.goal import GoalStore
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.workspace.recovery.coordinator import WorkspaceRecovery
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.events import RunResult
from cade.harness.loop import run
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog
from cade.harness.tools import ToolResult
from cade.hosts.cli import main


class FakeProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

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


def test_cli_final_usage_includes_compaction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = SessionLog.create(tmp_path / ".cade" / "sessions")
    log.append("user", {"text": "old task " * 10_000})
    provider = FakeProvider(
        [
            [UsageUpdate(100, 20), TextDelta("summary"), FinalMessage("", "end_turn")],
            [UsageUpdate(30, 4), TextDelta("done"), FinalMessage("", "end_turn")],
        ]
    )
    code = main(
        [
            "exec",
            "next task",
            "--project-root",
            str(tmp_path),
            "--resume",
            log.session_id,
            "--json",
            "--context-window",
            "30000",
        ],
        provider=provider,
    )
    assert code == 0
    final = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert final["termination"] == "completed"
    assert final["usage"] == {"input": 130, "output": 24}


def _text(text: str) -> list[ProviderEvent]:
    return [TextDelta(text), FinalMessage(text, "end_turn")]


def _session_files(sessions: Path) -> list[Path]:
    return sorted(sessions.glob("session-*.jsonl"))


@pytest.mark.parametrize("task_option", ["-p", "--prompt", "exec"])
def test_cli_one_shot_prints_answer_and_creates_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    task_option: str,
) -> None:
    sessions = tmp_path / "sessions"
    code = main(
        [
            task_option,
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
    assert types[-1] == "checkpoint"
    assert types.count("run") == 1


@pytest.mark.parametrize("task_option", ["-p", "exec"])
def test_cli_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], task_option: str
) -> None:
    code = main(
        [
            task_option,
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


@pytest.mark.parametrize("task_option", ["-p", "exec"])
def test_cli_resume_appends_to_same_session(tmp_path: Path, task_option: str) -> None:
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
            [task_option, "first", *common],
            provider=FakeProvider([_text("one")]),
        )
        == 0
    )
    session_id = _session_files(sessions)[0].stem.removeprefix("session-")
    assert (
        main(
            [task_option, "second", *common, "--resume", session_id],
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


def test_cli_interactive_can_start_disconnected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade.app.session import ApplicationSession, UnavailableProvider

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def unavailable(config: object) -> None:
        raise RuntimeError("Missing API key")

    def show(service: ApplicationSession, root: Path, **kwargs: Any) -> int:
        assert isinstance(service.provider, UnavailableProvider)
        assert not service.log.path.exists()
        return 0

    monkeypatch.setattr("cade.app.bootstrap.build_provider", unavailable)
    monkeypatch.setattr("cade.hosts.tui.app.run_tui", show)
    assert (
        main(
            [
                "--project-root",
                str(tmp_path),
                "--provider",
                "openai",
                "--model",
                "gpt-5.5",
            ]
        )
        == 0
    )


@pytest.mark.parametrize("sensitive", [False, True])
def test_tui_sensitive_approval_only_allows_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sensitive: bool
) -> None:
    from cade.app.session import ApplicationSession
    from cade.harness.session.types import ToolCallBlock
    from cade.hosts.tui.bridge import CadeAgent
    from cade.hosts.tui.compat import ApprovalRequest, HITLResult

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def show(service: ApplicationSession, root: Path, **kwargs: Any) -> int:
        agent = CadeAgent(service)
        call = ToolCallBlock(
            id="approval",
            name="bash",
            args={"command": "cat .env" if sensitive else "rm ordinary.txt"},
        )
        requests: list[ApprovalRequest] = []

        def approve(request: ApprovalRequest) -> HITLResult:
            requests.append(request)
            return HITLResult("allow", "session")

        agent.user_approval_callback = approve
        assert asyncio.run(agent.approve(call))
        assert requests[0].allowed_scopes == (
            ("once",) if sensitive else ("once", "session")
        )
        assert service.policy.grants.match(call) is not sensitive
        if sensitive:
            assert "sensitive" in requests[0].reason.lower()
            assert asyncio.run(agent.approve(call))
            assert len(requests) == 2
        return 0

    monkeypatch.setattr("cade.hosts.tui.app.run_tui", show)
    assert main(["--project-root", str(tmp_path)], provider=FakeProvider([])) == 0


def test_cli_positional_prompt_starts_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade.app.session import ApplicationSession

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    provider = FakeProvider([])

    def launch(service: ApplicationSession, root: Path, **kwargs: Any) -> int:
        assert kwargs["initial_prompt"] == "first message"
        assert not service.log.path.exists()
        assert not provider.requests
        return 0

    monkeypatch.setattr("cade.hosts.tui.app.run_tui", launch)
    assert (
        main(["first message", "--project-root", str(tmp_path)], provider=provider) == 0
    )


@pytest.mark.parametrize("arguments", [["-p"], ["first", "-p", "second"]])
def test_cli_rejects_missing_or_conflicting_prompt(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(arguments, provider=FakeProvider([]))
    assert error.value.code == 2


@pytest.mark.parametrize("arguments", [["-p", ""], ["exec", ""], ["   "]])
def test_cli_rejects_empty_prompt(arguments: list[str]) -> None:
    assert main(arguments, provider=FakeProvider([])) == 2


def test_cli_positional_prompt_requires_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    provider = FakeProvider([])
    assert (
        main(["first message", "--project-root", str(tmp_path)], provider=provider) == 2
    )
    assert "cade -p TASK" in capsys.readouterr().err
    assert not provider.requests


def test_cli_interactive_requires_terminal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
        ],
        provider=FakeProvider([]),
    )

    assert code == 2
    assert "requires a terminal" in capsys.readouterr().err


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


def test_cli_rejects_json_without_task(
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
    (workspace / "cade.json").write_text(
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
    run_entry = next(
        e for e in reversed(SessionLog.open(files[0]).branch()) if e.type == "run"
    )
    assert run_entry.data["termination"] == "step_limit"


def test_cli_config_can_disable_instructions(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("PROJECT RULE 42", encoding="utf-8")
    (workspace / "cade.json").write_text(
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
    (workspace / "cade.json").write_text(
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
    (tmp_path / "cade.json").write_text("{bad", encoding="utf-8")

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
    (workspace / "cade.json").write_text(
        '{"trust_project_skills": true}', encoding="utf-8"
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


def test_cli_uses_configured_prompt_modules(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "cade.json").write_text(
        json.dumps({"prompt_modules": ["identity"]}),
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
    assert "Cade" in system_text
    assert "Platform:" not in system_text


def test_cli_includes_working_note(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "NOTE.md").write_text("NOTE STATE: continue", encoding="utf-8")
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
    assert "NOTE STATE" in system_text


def test_cli_can_disable_note(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "NOTE.md").write_text("NOTE STATE: continue", encoding="utf-8")
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(workspace),
            "--yes",
            "--no-note",
        ],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "NOTE STATE" not in system_text


def test_cli_plan_mode_exposes_plan_tools_and_constraints(tmp_path: Path) -> None:
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--mode",
            "plan",
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--yes",
        ],
        provider=provider,
    )

    assert code == 0
    names = {tool.name for tool in provider.requests[0][1]}
    assert "read" in names
    assert "bash" in names
    assert "write" in names
    assert "edit" in names
    assert "Implementation requires" in str(provider.requests[0][0])


def test_cli_build_mode_allows_write_without_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
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
            "--mode",
            "build",
            "--project-root",
            str(workspace),
        ],
        provider=provider,
    )

    assert code == 0
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "x"


def test_cli_rule_allows_write_without_approval(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "cade.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "tool": "write",
                        "path_prefix": "docs/",
                        "decision": "allow",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="w1",
                            name="write",
                            input={"path": "docs/a.txt", "content": "x"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )

    code = main(
        ["exec", "write", "--project-root", str(workspace)],
        provider=provider,
    )

    assert code == 0
    assert (workspace / "docs" / "a.txt").read_text(encoding="utf-8") == "x"


def test_cli_runs_a_subagent(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="s1",
                            name="subagent",
                            input={"prompt": "child task"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("child answer"),
            _text("done"),
        ]
    )

    code = main(
        [
            "exec",
            "parent task",
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
    assert "child answer" in str(tool_result.data["content"])
    child_files = list((sessions / "subagents").glob("session-*.jsonl"))
    assert len(child_files) == 1
    child_names = {tool.name for tool in provider.requests[1][1]}
    assert "subagent" not in child_names


def test_cli_includes_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("- DURABLE FACT", encoding="utf-8")
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
    assert "DURABLE FACT" in system_text


def test_cli_can_disable_memory(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("- DURABLE FACT", encoding="utf-8")
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--project-root",
            str(workspace),
            "--yes",
            "--no-memory",
        ],
        provider=provider,
    )

    assert code == 0
    system_text = "\n".join(
        str(message.get("content", ""))
        for message in provider.requests[0][0]
        if message.get("role") == "system"
    )
    assert "DURABLE FACT" not in system_text


def test_cli_memory_tool_appends(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            id="m1",
                            name="memory",
                            input={"text": "new durable fact"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("done"),
        ]
    )

    code = main(
        ["exec", "remember", "--project-root", str(workspace), "--yes"],
        provider=provider,
    )

    assert code == 0
    assert "new durable fact" in (workspace / "MEMORY.md").read_text(encoding="utf-8")


@pytest.fixture
def recovery_log(tmp_path: Path) -> SessionLog:
    log = SessionLog.create(tmp_path / "sessions", project_root=tmp_path)
    log.append("system", {"text": "system prompt"})
    snapshots = SnapshotStore(tmp_path)
    recovery = WorkspaceRecovery(snapshots)
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("old", encoding="utf-8")
    with log.run_lock(), recovery.record(log):
        user = log.append("user", {"text": "change both files"})
        recovery.started(log, user)
        for name in ("a.txt", "b.txt"):
            target = tmp_path / name
            with snapshots.change(target):
                target.write_text("new", encoding="utf-8")
        log.append("assistant", {"blocks": [{"type": "text", "text": "done"}]})
    return log


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_undo_shares_application_recovery(
    tmp_path: Path,
    recovery_log: SessionLog,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    def unexpected_provider(config: object) -> None:
        raise AssertionError("Undo must not build a provider")

    monkeypatch.setattr("cade.app.bootstrap.build_provider", unexpected_provider)
    arguments = [
        "--undo",
        "--project-root",
        str(tmp_path),
        "--sessions-dir",
        "sessions",
    ]
    if explicit:
        arguments.extend(["--resume", recovery_log.session_id])
    assert main(arguments) == 0
    for name in ("a.txt", "b.txt"):
        assert (tmp_path / name).read_text(encoding="utf-8") == "old"
    with ExitStack() as resources:
        service = create_session(
            tmp_path,
            CadeConfig(sessions_dir="sessions"),
            resources=resources,
            provider=FakeProvider([]),
            interactive=True,
            resume=recovery_log.session_id,
        ).session
        result = asyncio.run(service.command("/redo"))
        assert result.lines[0].startswith("Redo applied")
        for name in ("a.txt", "b.txt"):
            assert (tmp_path / name).read_text(encoding="utf-8") == "new"
        result = asyncio.run(service.command("/undo"))
        assert result.lines[0].startswith("Undo applied")
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("restriction", [["--read-only"], ["--sandbox", "read-only"]])
def test_cli_undo_honors_read_only(
    tmp_path: Path, recovery_log: SessionLog, restriction: list[str]
) -> None:
    before = recovery_log.path.read_bytes()
    assert (
        main(
            [
                "--undo",
                "--project-root",
                str(tmp_path),
                "--sessions-dir",
                "sessions",
                *restriction,
            ]
        )
        == 1
    )
    assert recovery_log.path.read_bytes() == before
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


def test_cli_undo_does_not_fall_back_to_unowned_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    with SnapshotStore(tmp_path).change(target):
        target.write_text("new", encoding="utf-8")
    assert main(["--undo", "--project-root", str(tmp_path)]) == 1
    assert target.read_text(encoding="utf-8") == "new"


def test_cli_undo_rejects_foreign_workspace(
    tmp_path: Path, recovery_log: SessionLog
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    assert (
        main(
            [
                "--undo",
                "--project-root",
                str(other),
                "--sessions-dir",
                str(recovery_log.path.parent),
                "--resume",
                recovery_log.session_id,
            ]
        )
        == 1
    )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


def test_cli_sets_goal_in_context(tmp_path: Path) -> None:
    provider = FakeProvider([_text("ok")])

    code = main(
        [
            "exec",
            "hi",
            "--goal",
            "ship milestone 6",
            "--project-root",
            str(tmp_path),
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
    assert "ship milestone 6" in system_text
    goal = GoalStore(tmp_path).load()
    assert goal is not None
    assert goal.objective == "ship milestone 6"


def test_cli_auto_review_allows_write(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
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
            _text('{"allowed":true,"reason":"The user requested this file change."}'),
            _text("done"),
        ]
    )

    code = main(
        [
            "exec",
            "write",
            "--auto-review",
            "--mode",
            "ask",
            "--project-root",
            str(workspace),
        ],
        provider=provider,
    )

    assert code == 0
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "x"


def test_mid_run_steer_reaches_the_next_provider_request(tmp_path: Path) -> None:
    steering: deque[str] = deque()
    started = threading.Event()
    results: list[RunResult] = []

    class SlowTool:
        name = "read"
        description = "slow read"
        parameters: ClassVar[dict[str, Any]] = {
            "type": "object",
            "properties": {},
        }

        async def execute(self, call, signal=None) -> ToolResult:
            started.set()
            deadline = time.monotonic() + 2
            while not steering and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            return ToolResult(content="ok")

    provider = FakeProvider(
        [
            [ToolCallEvent([ToolCall(id="r1", name="read", input={"path": "a.txt"})])],
            _text("finished"),
        ]
    )
    log = SessionLog.create(tmp_path / "sessions")

    def worker() -> None:
        results.append(
            asyncio.run(
                run(
                    log,
                    "inspect the code",
                    provider=provider,
                    tools=(SlowTool(),),
                    policy=WorkspacePolicy(tmp_path),
                    budget=Budget(100_000),
                    max_steps=4,
                    steering=lambda: steering.popleft() if steering else None,
                )
            )
        )

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(timeout=1)
    steering.append("focus on the parser")
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert results[0].termination == "completed"
    users = [entry.data.get("text") for entry in log.branch() if entry.type == "user"]
    assert users == ["inspect the code", "focus on the parser"]
    assert "focus on the parser" in json.dumps(provider.requests[1][0])
