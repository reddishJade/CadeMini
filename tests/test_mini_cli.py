"""Cade Mini CLI 的回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cade_mini.ai.events import (
    FinalMessage,
    ProviderEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade_mini.ai.types import StreamOptions, ToolDefinition
from cade_mini.core.log import SessionLog
from cade_mini.core.loop import CancellationToken, RunResult, ToolResult
from cade_mini.core.permissions import ApprovalRequest, ApprovalResult
from cade_mini.core.request import Budget
from cade_mini.core.types import ToolCallBlock
from cade_mini.extensions.goal import GoalStore
from cade_mini.extensions.skills import Skill
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.hosts.cli import (
    _ApprovalRequest,
    _expand_input,
    _HostApprover,
    _Line,
    _run_shell_input,
    _run_task,
    _run_task_live,
    _TaskDone,
    main,
)
from cade_mini.safety.grants import GrantStore
from cade_mini.safety.policy import WorkspacePolicy
from cade_mini.tools.workspace import BashTool


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
    log = SessionLog.create(tmp_path / ".cade-mini" / "sessions")
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
    skill_dir = workspace / ".cade-mini" / "skills" / "greeter"
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


def test_cli_uses_configured_prompt_modules(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "cade-mini.json").write_text(
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
    assert "Cade Mini" in system_text
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
    (workspace / "cade-mini.json").write_text(
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


def test_cli_undo_restores_latest_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    with SnapshotStore(tmp_path).change(target):
        target.write_text("new", encoding="utf-8")

    code = main(["--undo", "--project-root", str(tmp_path)])

    assert code == 0
    assert target.read_text(encoding="utf-8") == "old"


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


def test_expand_file_and_skill_references(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("file body", encoding="utf-8")
    skill = Skill(
        name="greeter",
        description="Say hello",
        body="Say HELLO.",
        path=tmp_path / "SKILL.md",
    )

    expanded = _expand_input(
        "Review @notes.txt and use $greeter",
        tmp_path,
        [skill],
    )

    assert expanded is not None
    assert "file body" in expanded
    assert "Say HELLO." in expanded
    assert "Review" in expanded


def test_shell_input_runs_with_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool = BashTool(tmp_path)
    policy = WorkspacePolicy(tmp_path, mode="build")

    _run_shell_input(
        "echo hello-shell",
        [tool],
        policy,
        lambda request: ApprovalResult(True, "Approved for test"),
    )

    assert "hello-shell" in capsys.readouterr().out


def test_live_input_handles_steer_and_queue() -> None:
    events: queue.Queue[object] = queue.Queue()
    pending: deque[str] = deque()
    steering: deque[str] = deque()
    cancel = CancellationToken()

    def worker() -> None:
        events.put(_TaskDone(RunResult("", 1, "completed")))

    events.put(_Line("/steer focus on the parser"))
    events.put(_Line("/queue add a test"))
    result, exit_requested = _run_task_live(
        events,
        pending,
        steering,
        cancel,
        threading.Thread(target=worker),
    )

    assert result.termination == "completed"
    assert exit_requested is False
    assert list(steering) == ["focus on the parser"]
    assert list(pending) == ["add a test"]


def test_host_approver_routes_through_events() -> None:
    events: queue.Queue[object] = queue.Queue()
    grants = GrantStore()
    approver = _HostApprover(events, grants)

    def answer() -> None:
        request = events.get()
        assert isinstance(request, _ApprovalRequest)
        request.answer = "y"
        request.done.set()

    threading.Thread(target=answer, daemon=True).start()
    assert (
        approver(
            ApprovalRequest(
                ToolCallBlock(id="c1", name="write", args={"path": "a.txt"}),
                "Change file",
            )
        ).allowed
        is True
    )


def test_live_input_can_stop_the_task() -> None:
    events: queue.Queue[object] = queue.Queue()
    cancel = CancellationToken()

    def worker() -> None:
        cancel.cancel("stopped")
        events.put(_TaskDone(RunResult("", 1, "aborted")))

    events.put(_Line("/stop"))
    result, exit_requested = _run_task_live(
        events,
        deque(),
        deque(),
        cancel,
        threading.Thread(target=worker),
    )

    assert result.termination == "aborted"
    assert exit_requested is False


def test_live_input_is_read_while_worker_is_running() -> None:
    events: queue.Queue[object] = queue.Queue()
    steering: deque[str] = deque()
    started = threading.Event()

    def worker() -> None:
        started.set()
        deadline = time.monotonic() + 2
        while not steering and time.monotonic() < deadline:
            time.sleep(0.01)
        events.put(_TaskDone(RunResult("", 1, "completed")))

    def feed() -> None:
        started.wait(timeout=1)
        events.put(_Line("/steer focus from the host"))

    threading.Thread(target=feed, daemon=True).start()
    result, _ = _run_task_live(
        events,
        deque(),
        steering,
        CancellationToken(),
        threading.Thread(target=worker),
    )

    assert result.termination == "completed"
    assert list(steering) == ["focus from the host"]


def test_mid_run_steer_reaches_the_next_provider_request(tmp_path: Path) -> None:
    events: queue.Queue[object] = queue.Queue()
    steering: deque[str] = deque()
    started = threading.Event()

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
        result = _run_task(
            log,
            "inspect the code",
            provider,
            (SlowTool(),),
            WorkspacePolicy(tmp_path),
            lambda call: True,
            Budget(100_000),
            (),
            4,
            lambda event: None,
            None,  # type: ignore[arg-type]
            None,
            None,
            lambda: steering.popleft() if steering else None,
            CancellationToken(),
        )
        events.put(_TaskDone(result))

    def feed() -> None:
        started.wait(timeout=1)
        events.put(_Line("/steer focus on the parser"))

    threading.Thread(target=feed, daemon=True).start()
    result, _ = _run_task_live(
        events,
        deque(),
        steering,
        CancellationToken(),
        threading.Thread(target=worker),
    )

    assert result.termination == "completed"
    users = [entry.data.get("text") for entry in log.branch() if entry.type == "user"]
    assert users == ["inspect the code", "focus on the parser"]
    assert "focus on the parser" in json.dumps(provider.requests[1][0])


def test_repl_reads_steer_while_a_bash_tool_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = f'"{sys.executable}" -c "import time; time.sleep(0.2)"'
    provider = FakeProvider(
        [
            [
                ToolCallEvent(
                    [ToolCall(id="b1", name="bash", input={"command": command})]
                )
            ],
            _text("finished"),
        ]
    )
    lines = iter(["inspect", "/steer focus on the parser", "/exit"])

    def fake_input(_prompt: str = "") -> str:
        value = next(lines, "/exit")
        if value == "/exit":
            time.sleep(0.5)
        return value

    monkeypatch.setattr("builtins.input", fake_input)
    code = main(
        [
            "--project-root",
            str(tmp_path),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--yes",
            "--max-steps",
            "4",
        ],
        provider=provider,
    )

    assert code == 0
    paths = _session_files(tmp_path / "sessions")
    assert len(paths) == 1
    log = SessionLog.open(paths[0])
    users = [entry.data.get("text") for entry in log.branch() if entry.type == "user"]
    assert users == ["inspect", "focus on the parser"]
