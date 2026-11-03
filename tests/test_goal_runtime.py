"""目标续跑、预算、宿主调度与恢复的回归测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import (
    FinalMessage,
    ProviderEvent,
    ProviderFailure,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    UsageUpdate,
)
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.session import ApplicationSession
from cade.coding.goal import GoalStore
from cade.harness.cancellation import CancellationToken
from cade.harness.events import LoopEvent, TextDeltaEvent
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog, list_sessions
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import ToolResult
from cade.hosts.tui.bridge import CadeInteractionApp


class Provider:
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
        for event in self.batches.pop(0):
            yield event


def _text(text: str) -> list[ProviderEvent]:
    return [UsageUpdate(5, 2), TextDelta(text), FinalMessage(text, "end_turn")]


def _complete(session: ApplicationSession) -> list[ProviderEvent]:
    goal = session.goals.load()
    assert goal is not None
    return [
        ToolCallEvent(
            [
                ToolCall(
                    id="complete",
                    name="goal",
                    input={"action": "complete", "updated_at": goal.updated_at},
                )
            ]
        ),
        FinalMessage("", "tool_use"),
    ]


def test_goal_continues_after_an_early_final_and_keeps_one_run_budget(
    tmp_path: Path,
) -> None:
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish all backend requirements",
        ).session
        provider.batches = [
            _text("first part done"),
            _text("verification remains"),
            _complete(session),
            _text("all verified"),
        ]
        result = asyncio.run(session.run_task("start", lambda _: None))
        assert result.termination == "completed"
        assert result.answer == "all verified"
        assert result.steps == 4
        assert result.usage == {"input": 15, "output": 6}
        assert session.goals.load().status == "done"
        entries = session.log.branch()
        assert [entry.data["source"] for entry in entries if entry.type == "user"] == [
            "user",
            "goal",
            "goal",
        ]
        assert len([entry for entry in entries if entry.type == "run"]) == 1
        assert "verify every requirement" in str(provider.requests[1])
        assert session.resolve_turn("1").data["text"] == "start"
        assert len(session.choices("fork")) == 1


def test_goal_step_limit_disarms_without_claiming_completion(tmp_path: Path) -> None:
    provider = Provider()
    provider.batches = [_text("work remains") for _ in range(3)]
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(max_steps=3, sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="complete backend",
        ).session
        result = asyncio.run(session.run_task("start", lambda _: None))
        assert result.termination == "step_limit"
        assert result.steps == 3
        assert result.usage == {"input": 15, "output": 6}
        assert len(provider.requests) == 3
        assert session.goals.load().status == "active"
        assert session.goal_input() is None
        assert not CadeInteractionApp(session).agent.has_pending_input()


@pytest.mark.parametrize("cancel", [False, True])
def test_goal_error_or_cancellation_does_not_restart(
    tmp_path: Path, cancel: bool
) -> None:
    provider = Provider()
    provider.batches = (
        [_text("working")]
        if cancel
        else [[ProviderFailure("unauthorized", "Auth", 401)]]
    )
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="complete backend",
        ).session

        def event(event: LoopEvent) -> None:
            if cancel and isinstance(event, TextDeltaEvent):
                assert session.cancel is not None
                session.cancel.cancel("interrupted")

        result = asyncio.run(session.run_task("start", event))
        assert result.termination == ("aborted" if cancel else "provider_error")
        assert session.goals.runnable() is None
        assert session.goals.load().status == "active"
        assert len(provider.requests) == 1


def test_persisted_goal_requires_resume_and_tui_schedules_it(tmp_path: Path) -> None:
    GoalStore(tmp_path).set("finish backend")
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
        ).session
        app = CadeInteractionApp(session)
        assert not app.agent.has_pending_input()
        provider.batches = [_text("human task done")]
        assert (
            asyncio.run(session.run_task("unrelated task", lambda _: None)).termination
            == "completed"
        )
        assert len(provider.requests) == 1
        asyncio.run(session.command("/goal resume"))
        assert app.agent.has_pending_input()
        provider.batches = [_complete(session), _text("backend verified")]
        list(app.ask_stream(None))
        assert session.goals.load().status == "done"
        assert not app.agent.has_pending_input()
        users = [entry for entry in session.log.branch() if entry.type == "user"]
        assert users[-1].data["source"] == "goal"


def test_goal_yields_to_queued_human_input(tmp_path: Path) -> None:
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish backend",
        ).session
        session.pending.append("inspect this new issue first")
        provider.batches = [_text("first part done")]
        result = asyncio.run(session.run_task("start", lambda _: None))
        assert result.termination == "completed"
        assert len(provider.requests) == 1
        assert session.goals.runnable() is not None
        provider.batches = [_complete(session), _text("all done")]
        list(CadeInteractionApp(session).ask_stream(None))
        users = [entry for entry in session.log.branch() if entry.type == "user"]
        assert users[-1].data == {
            "text": "inspect this new issue first",
            "source": "user",
        }


def test_goal_pause_and_history_navigation_disarm(tmp_path: Path) -> None:
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish backend",
        ).session
        assert "not supported" not in str(asyncio.run(session.command("/goal pause")))
        assert session.goals.load().status == "paused"
        assert session.goal_input() is None
        asyncio.run(session.command("/goal resume"))
        assert session.goal_input() is not None
        asyncio.run(session.command("/new"))
        assert session.goal_input() is None
        assert session.goals.load().status == "active"


def test_automatic_goal_crosses_windows_before_completion(tmp_path: Path) -> None:
    class EvidenceTool:
        name = "read"
        description = "Return evidence."

        def __init__(self) -> None:
            self.parameters = {"type": "object", "properties": {}}

        async def execute(
            self, call: ToolCallBlock, signal: CancellationToken | None = None
        ) -> ToolResult:
            return ToolResult(content="verification evidence " * 2000)

    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish backend and verify migrations",
        ).session
        goal_tool = next(tool for tool in session.base_tools if tool.name == "goal")
        session.base_tools = (EvidenceTool(), goal_tool)
        session.budget = Budget(5000, output_reserve=0, headroom_tokens=0)
        provider.batches = [
            [ToolCallEvent([ToolCall("e1", "read", {})]), FinalMessage("", "tool_use")],
            _text("first part done"),
            [ToolCallEvent([ToolCall("e2", "read", {})]), FinalMessage("", "tool_use")],
            _complete(session),
            _text("all verified"),
        ]
        list(CadeInteractionApp(session).ask_stream(None))
        assert session.goals.load().status == "done"
        entries = session.log.branch()
        assert len([entry for entry in entries if entry.type == "window"]) == 2
        assert (
            len(
                [
                    entry
                    for entry in entries
                    if entry.type == "tool_result"
                    and entry.data["call_id"] in {"e1", "e2"}
                ]
            )
            == 2
        )
        for messages in provider.requests:
            assert "finish backend and verify migrations" in str(messages)


@pytest.mark.parametrize(
    "config", [CadeConfig(read_only=True), CadeConfig(mode="plan")]
)
def test_goal_does_not_loop_when_policy_denies_completion(
    tmp_path: Path, config: CadeConfig
) -> None:
    provider = Provider()
    provider.batches = [_text("plan ready")]
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            config,
            resources=resources,
            provider=provider,
            goal="plan backend",
        ).session
        result = asyncio.run(session.run_task("make the plan", lambda _: None))
        assert result.termination == "completed"
        assert len(provider.requests) == 1
        assert session.goals.load().status == "active"
        assert session.goals.runnable() is None


def test_goal_only_session_is_discoverable_and_writes_can_be_undone(
    tmp_path: Path,
) -> None:
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="write result and verify",
            interactive=True,
            auto_approve=True,
        ).session
        provider.batches = [
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            "w1", "write", {"path": "result.txt", "content": "verified"}
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _complete(session),
            _text("verified"),
        ]
        list(CadeInteractionApp(session).ask_stream(None))
        assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "verified"
        saved = SessionLog.open(session.log.path)
        assert any(
            entry.type == "user" and entry.data["source"] == "goal"
            for entry in saved.branch()
        )
        assert [item.id for item in list_sessions(session.sessions_dir)] == [
            session.log.session_id
        ]
        asyncio.run(session.command("/undo"))
        assert not (tmp_path / "result.txt").exists()


def test_goal_blocker_is_persisted_and_stops_continuation(tmp_path: Path) -> None:
    provider = Provider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish deployment",
        ).session
        goal = session.goals.load()
        provider.batches = [
            _text("checking deployment credentials"),
            _text("checking available alternatives"),
            [
                ToolCallEvent(
                    [
                        ToolCall(
                            "block",
                            "goal",
                            {
                                "action": "block",
                                "updated_at": goal.updated_at,
                                "reason": "deployment credentials are unavailable",
                            },
                        )
                    ]
                ),
                FinalMessage("", "tool_use"),
            ],
            _text("Deployment requires credentials."),
        ]
        result = asyncio.run(session.run_task("start", lambda _: None))
        assert result.termination == "completed"
        assert session.goals.load().status == "blocked"
        assert session.goal_input() is None
        assert "deployment credentials are unavailable" in str(
            asyncio.run(session.command("/goal"))
        )


def test_pause_command_cancels_the_running_goal(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class WaitingProvider(Provider):
        async def stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
        ) -> AsyncIterator[ProviderEvent]:
            self.requests.append(messages)
            yield TextDelta("working")
            started.set()
            await release.wait()
            yield FinalMessage("working", "end_turn")

    provider = WaitingProvider()
    with ExitStack() as resources:
        session = create_session(
            tmp_path,
            CadeConfig(sandbox="danger-full-access"),
            resources=resources,
            provider=provider,
            goal="finish backend",
        ).session

        async def scenario() -> None:
            running = asyncio.create_task(session.run_task("start", lambda _: None))
            await asyncio.wait_for(started.wait(), timeout=5)
            await session.command("/goal pause")
            with pytest.raises(RuntimeError, match="Wait for the active task"):
                await session.command("/goal resume")
            release.set()
            result = await asyncio.wait_for(running, timeout=5)
            assert result.termination == "aborted"
            assert session.goals.load().status == "paused"
            assert session.goal_input() is None
            assert len(provider.requests) == 1

        asyncio.run(scenario())
