"""验证 TUI 入口遵守应用审批、输入和恢复约定。"""

from __future__ import annotations

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
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.session import ApplicationSession
from cade.harness.permissions import ApprovalRequest, ApprovalResult
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock
from cade.hosts.tui.app import _CadeTui
from cade.hosts.tui.bridge import CadeInteractionApp
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
        [ToolCallEvent([ToolCall("child", "subagent", {"prompt": "write a file"})])],
        [
            ToolCallEvent(
                [ToolCall("write", "write", {"path": "child.txt", "content": "ok"})]
            )
        ],
        [FinalMessage("child done", "end_turn")],
        [FinalMessage("parent done", "end_turn")],
    ]
    approvals: list[str] = []

    async def manual(call: ToolCallBlock) -> bool:
        assert not automatic
        approvals.append(call.name)
        return True

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
        [TextDelta("failed text"), ProviderFailure("timeout", "TimeoutError")],
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
