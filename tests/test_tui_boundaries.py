"""验证 TUI 入口遵守应用审批、输入和恢复约定。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.session import ApplicationSession
from cade.coding.security.review import AutoReviewer
from cade.harness.events import LoopEvent, ToolEndEvent
from cade.harness.permissions import ApprovalRequest, ApprovalResult
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock
from cade.hosts.tui.app import _CadeTui
from cade.hosts.tui.bridge import CadeInteractionApp
from cade.hosts.tui.compat import HITLResult
from cade.hosts.tui.interaction_compat import run_shell_shortcut
from cade.hosts.tui.state import _TuiState


class ScriptedProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

    def __init__(self) -> None:
        self.batches: list[list[ProviderEvent]] = []
        self.requests: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(messages)
        batch = (
            self.batches.pop(0) if self.batches else [FinalMessage("done", "end_turn")]
        )
        for event in batch:
            yield event


@pytest.fixture
def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ApplicationSession]:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    with ExitStack() as resources:
        yield create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            provider=ScriptedProvider(),
            resources=resources,
        ).session


def test_tui_preserves_configured_plan_mode(session: ApplicationSession) -> None:
    session.policy.set_mode("plan")
    with create_pipe_input() as pipe:
        tui = _CadeTui(
            CadeInteractionApp(session),
            session.workspace,
            input=pipe,
            output=DummyOutput(),
        )
        assert tui._interaction_state.mode == "plan"
        assert tui._state.mode == "plan"
        list(tui._agent_app.ask_stream("hello", mode=tui._interaction_state.mode))
    assert session.policy.mode == "plan"


@pytest.mark.parametrize(
    ("batch", "expected"),
    [
        (
            [
                TextDelta("hello"),
                UsageUpdate(50, 100),
                FinalMessage("hello", "end_turn"),
            ],
            "TTFT 2.00s · TPS 20.0",
        ),
        (
            [
                TextDelta(""),
                ReasoningDelta("request reasoning summary 73"),
                TextDelta("hello"),
                UsageUpdate(50, 100),
                FinalMessage("hello", "end_turn"),
            ],
            "TTFT 2.00s · TPS 20.0",
        ),
        ([TextDelta("hello"), FinalMessage("hello", "end_turn")], "TTFT 2.00s · TPS —"),
        (
            [TextDelta("hello"), UsageUpdate(50, 0), FinalMessage("hello", "end_turn")],
            "TTFT 2.00s · TPS —",
        ),
        (
            [
                TextDelta("partial"),
                UsageUpdate(50, 100),
                FinalMessage("partial", "error"),
            ],
            "TTFT 2.00s · TPS —",
        ),
        (
            [
                TextDelta("partial"),
                UsageUpdate(50, 100),
                FinalMessage("partial", "cancelled"),
            ],
            "TTFT 2.00s · TPS —",
        ),
        ([FinalMessage("hello", "end_turn")], None),
    ],
)
def test_tui_displays_request_metrics(
    session: ApplicationSession,
    monkeypatch: pytest.MonkeyPatch,
    batch: list[ProviderEvent],
    expected: str | None,
) -> None:
    clock = iter([10.0, 12.0, 17.0])
    monkeypatch.setattr("cade.harness.loop.perf_counter", lambda: next(clock))
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    provider.batches = [batch]
    state = _TuiState()
    for event in CadeInteractionApp(session).ask_stream("hello"):
        state.handle_event(event)
        if event.type == "response_metrics" and not event.data.completed:
            assert "TTFT 2.00s · TPS —" in state.render()
    session.log = SessionLog.open(session.log.path)
    restored = _TuiState()
    restored.restore_history(CadeInteractionApp(session).session_store.build_branch())
    if expected is None:
        assert "TTFT" not in state.render()
        assert "TTFT" not in restored.render()
    else:
        assert expected in state.render()
        assert restored.render().count(expected) == 1
    if any(isinstance(event, ReasoningDelta) for event in batch):
        assert "request reasoning summary 73" in restored.render()
        assert "request reasoning summary 73" not in str(
            [message.model_dump() for message in project(session.log.branch())]
        )


def test_tui_file_input_is_expanded_once(session: ApplicationSession) -> None:
    content = "$not_a_skill @not-a-file\nunique file body"
    (session.workspace / "input.txt").write_text(content, encoding="utf-8")
    state = _TuiState()
    app = CadeInteractionApp(session)
    for event in app.ask_stream("check @input.txt"):
        state.handle_event(event)
    user = next(entry for entry in session.log.branch() if entry.type == "user")
    assert str(user.data["text"]).count(content) == 1
    request = next(entry for entry in session.log.branch() if entry.type == "request")
    assert str(request.data["snapshot"]).count("unique file body") == 1


@pytest.mark.parametrize("automatic", [False, True])
def test_tui_child_inherits_selected_approver(
    session: ApplicationSession, automatic: bool
) -> None:
    session.policy.set_mode("ask")
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    provider.batches = [
        [ToolCallEvent([ToolCall("child", "delegate", {"prompt": "write a file"})])],
        [
            ToolCallEvent(
                [ToolCall("write", "write", {"path": "child.txt", "content": "ok"})]
            )
        ],
        [FinalMessage("child done", "end_turn")],
        [FinalMessage("parent done", "end_turn")],
    ]
    approvals: list[str] = []

    async def manual(call: ToolCallBlock) -> ApprovalResult:
        assert not automatic
        approvals.append(call.name)
        return ApprovalResult(True, "approved by user")

    async def reviewer(request: ApprovalRequest) -> ApprovalResult:
        approvals.append(request.call.name)
        return ApprovalResult(True, "approved by test reviewer")

    if automatic:
        session.automatic_approver = reviewer
    app = CadeInteractionApp(session)
    app.agent.approve = manual
    list(app.ask_stream("delegate the work"))
    assert approvals == ["write"]
    assert (session.workspace / "child.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.parametrize("child", [False, True])
def test_tui_denial_suggestion_reaches_model(
    session: ApplicationSession, child: bool
) -> None:
    session.policy.set_mode("ask")
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    write = [
        ToolCallEvent(
            [ToolCall("write", "write", {"path": "denied.txt", "content": "x"})]
        ),
        FinalMessage("", "tool_use"),
    ]
    provider.batches = [write, [FinalMessage("understood", "end_turn")]]
    if child:
        provider.batches.insert(
            0,
            [
                ToolCallEvent(
                    [ToolCall("child", "delegate", {"prompt": "write a file"})]
                ),
                FinalMessage("", "tool_use"),
            ],
        )
        provider.batches.append([FinalMessage("parent done", "end_turn")])
    suggestion = "Use the read tool instead; do not create files."
    app = CadeInteractionApp(session)
    app.agent.user_approval_callback = lambda request: HITLResult(
        "deny", suggestion=suggestion
    )
    list(app.ask_stream("inspect the workspace"))
    assert not (session.workspace / "denied.txt").exists()
    assert any(
        suggestion in str(message.get("content", ""))
        for request in provider.requests
        for message in request
        if message.get("role") == "tool"
    )
    logs = [session.log]
    if child:
        logs.extend(
            SessionLog.open(path)
            for path in (session.sessions_dir / "subagents").glob("session-*.jsonl")
        )
    assert any(
        suggestion in str(entry.data["content"])
        for log in logs
        for entry in log.branch()
        if entry.type == "tool_result"
    )


@pytest.mark.parametrize("failed", [False, True])
def test_tui_displays_delegate_task_progress(
    session: ApplicationSession, failed: bool
) -> None:
    (session.workspace / "input.txt").write_text("input", encoding="utf-8")
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    provider.batches = [
        [
            ToolCallEvent(
                [
                    ToolCall(
                        "children",
                        "delegate",
                        {"tasks": ["read input", "check result"]},
                    )
                ]
            ),
            FinalMessage("", "tool_use"),
        ],
        [
            ToolCallEvent([ToolCall("read", "read", {"path": "input.txt"})]),
            FinalMessage("", "tool_use"),
        ],
        [FinalMessage("first done", "max_tokens" if failed else "end_turn")],
        [FinalMessage("second done", "end_turn")],
        [FinalMessage("parent done", "end_turn")],
    ]
    state = _TuiState()
    updates: list[str] = []
    for event in CadeInteractionApp(session).ask_stream("delegate the checks"):
        state.handle_event(event)
        if event.type == "tool_update":
            assert event.data.tool_name == "delegate"
            updates.append(event.data.partial_result)
            if event.data.partial_result == "[1]   read":
                assert "● Subagents" in state.render()
                assert state.subagents[1].tool == "read"
    marker = "✗" if failed else "✓"
    assert updates == [
        "[1] → read input",
        "[1]   read",
        f"[1] {marker} read input",
        "[2] → check result",
        "[2] ✓ check result",
    ]
    assert state.subagents[1].task == f"{marker} read input"
    assert state.subagents[2].task == "✓ check result"
    assert all(not slot.tool for slot in state.subagents.values())


@pytest.mark.parametrize("direct", [False, True])
def test_tui_displays_and_restores_automatic_approval_reason(
    session: ApplicationSession, direct: bool
) -> None:
    session.policy.set_mode("ask")
    reviewer = ScriptedProvider()
    reviewer.batches = [
        [FinalMessage('{"allowed":true,"reason":"Requested file change"}', "end_turn")]
    ]
    session.automatic_approver = AutoReviewer(reviewer, workspace=session.workspace)
    app = CadeInteractionApp(session)
    if direct:
        events: list[LoopEvent] = []
        result = asyncio.run(
            session.execute_tool(
                "write",
                {"path": "approved.txt", "content": "approved"},
                app.agent.approve,
                events.append,
            )
        )
        assert not result.is_error
        completed = [event for event in events if isinstance(event, ToolEndEvent)]
        assert len(completed) == 1
        assert (
            "Reviewer approved: Requested file change"
            in completed[0].authorization_reason
        )
    else:
        provider = session.provider
        assert isinstance(provider, ScriptedProvider)
        provider.batches = [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            "write",
                            "write",
                            {"path": "approved.txt", "content": "approved"},
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            [FinalMessage("done", "end_turn")],
        ]
        live = _TuiState()
        for event in app.ask_stream("write the file"):
            live.handle_event(event)
        assert live.render().count("Reviewer approved: Requested file change") == 1
    assert (session.workspace / "approved.txt").read_text(
        encoding="utf-8"
    ) == "approved"
    session.log = SessionLog.open(session.log.path)
    restored = _TuiState()
    restored.restore_history(app.session_store.build_branch())
    assert restored.render().count("Reviewer approved: Requested file change") == 1


@pytest.mark.parametrize(
    ("termination", "error", "expected"),
    [
        ("completed", None, None),
        ("max_tokens", None, "[stopped] output token limit reached"),
        ("aborted", "cancelled", "[stopped] cancelled"),
        ("provider_error", "service unavailable", "[stopped] service unavailable"),
        ("context_error", "request exceeds budget", "[stopped] request exceeds budget"),
        ("step_limit", None, "[stopped] step limit reached"),
    ],
)
def test_tui_restores_run_outcomes_without_duplicate_answers(
    session: ApplicationSession,
    termination: str,
    error: str | None,
    expected: str | None,
) -> None:
    first = session.log.append("user", {"text": "first task"})
    session.log.append(
        "assistant", {"blocks": [{"type": "text", "text": "first answer"}]}
    )
    session.log.append("run", {"termination": "completed", "steps": 1})
    session.log.append("user", {"text": "second task"})
    session.log.append(
        "assistant", {"blocks": [{"type": "text", "text": "second answer"}]}
    )
    session.log.append("run", {"termination": termination, "steps": 1, "error": error})
    session.log = SessionLog.open(session.log.path)
    app = CadeInteractionApp(session)
    state = _TuiState()
    state.restore_history(app.session_store.build_branch())
    assert state.render().count("first answer") == 1
    assert state.render().count("second answer") == 1
    if expected:
        assert state.render().count(expected) == 1
        assert state.render().index(expected) > state.render().index("second answer")
    else:
        assert "[stopped]" not in state.render()
    session.log.switch_branch(first.id)
    state.restore_history(app.session_store.build_branch())
    assert "[stopped]" not in state.render()
    assert "second answer" not in state.render()


def test_failed_stream_is_saved_before_recovery_seals(
    session: ApplicationSession,
) -> None:
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    provider.batches = [
        [
            TextDelta("partial answer"),
            ProviderFailure("bad request", "BadRequest", status_code=400),
        ]
    ]
    state = _TuiState()
    for event in CadeInteractionApp(session).ask_stream("hello"):
        state.handle_event(event)
    history = session.recovery.store.history(session.log.session_id)
    assert history.head_id == session.log.head_id
    assistants = [entry for entry in session.log.branch() if entry.type == "assistant"]
    assert len(assistants) == 1
    reopened = SessionLog.open(session.log.path)
    assert project(reopened.branch()) == project(session.log.branch())
    assert "Undo applied" in session.recovery.step(session.log, "undo")
    assert "Redo applied" in session.recovery.step(session.log, "redo")
    assert [entry.content() for entry in state.log if entry.role == "cade"] == [
        "partial answer"
    ]


def test_retry_discards_failed_display_and_keeps_one_answer(
    session: ApplicationSession,
) -> None:
    provider = session.provider
    assert isinstance(provider, ScriptedProvider)
    provider.batches = [
        [
            ReasoningDelta("failed reasoning"),
            TextDelta("failed text"),
            ProviderFailure("timeout", "TimeoutError"),
        ],
        [TextDelta("success"), FinalMessage("success", "end_turn")],
    ]
    state = _TuiState()
    for event in CadeInteractionApp(session).ask_stream("hello"):
        state.handle_event(event)
    assert [entry.content() for entry in state.log if entry.role == "cade"] == [
        "success"
    ]
    assistants = [entry for entry in session.log.branch() if entry.type == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].data["blocks"] == [{"type": "text", "text": "success"}]
    assert state.render().count("TTFT") == 1
    session.log = SessionLog.open(session.log.path)
    restored = _TuiState()
    restored.restore_history(CadeInteractionApp(session).session_store.build_branch())
    assert "failed text" not in restored.render()
    assert "failed reasoning" not in restored.render()
    assert restored.render().count("TTFT") == 1


@pytest.mark.parametrize("restriction", ["plan", "read_only"])
def test_shell_shortcut_obeys_session_restrictions(
    session: ApplicationSession, restriction: str
) -> None:
    if restriction == "plan":
        session.policy.set_mode("plan")
    else:
        session.policy.read_only = True
    output = run_shell_shortcut(
        "!echo forbidden > forbidden.txt", CadeInteractionApp(session)
    )
    assert "Permission denied" in output
    assert not (session.workspace / "forbidden.txt").exists()
    assert not session.running
    assert session.cancel is None


def test_shell_shortcut_records_recoverable_changes(
    session: ApplicationSession,
) -> None:
    output = run_shell_shortcut("!echo done > made.txt", CadeInteractionApp(session))
    assert "Permission denied" not in output
    assert (session.workspace / "made.txt").is_file()
    assert any(entry.type == "tool_result" for entry in session.log.branch())
    assert "Undo applied" in session.recovery.step(session.log, "undo")
    assert not (session.workspace / "made.txt").exists()
    assert "Redo applied" in session.recovery.step(session.log, "redo")
    assert (session.workspace / "made.txt").is_file()
