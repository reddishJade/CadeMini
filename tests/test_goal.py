"""Cade 目标存储与工具的回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from cade.coding.goal import GoalStore, GoalTool
from cade.coding.security.policy import WorkspacePolicy
from cade.harness.session.types import JsonValue, ToolCallBlock


def _call(
    action: str, objective: str | None = None, *, updated_at: str | None = None
) -> ToolCallBlock:
    args: dict[str, JsonValue] = {"action": action}
    if objective is not None:
        args["objective"] = objective
    if updated_at is not None:
        args["updated_at"] = updated_at
    return ToolCallBlock(id="g1", name="goal", args=args)


def test_goal_store_lifecycle(tmp_path: Path) -> None:
    store = GoalStore(tmp_path)

    assert store.load() is None
    goal = store.set("ship milestone 6")
    assert goal.status == "active"
    assert store.load() == goal

    done = store.complete()
    assert done is not None
    assert done.status == "done"

    store.clear()
    assert store.load() is None


def test_goal_tool_actions(tmp_path: Path) -> None:
    tool = GoalTool(tmp_path)

    empty = asyncio.run(tool.execute(_call("get")))
    assert "No goal" in empty.content

    set_result = asyncio.run(tool.execute(_call("set", "finish the host")))
    assert "finish the host" in set_result.content

    get_result = asyncio.run(tool.execute(_call("get")))
    assert "active: finish the host" in get_result.content

    done = asyncio.run(
        tool.execute(
            _call("complete", updated_at=str(get_result.metadata["updated_at"]))
        )
    )
    assert "completed" in done.content

    cleared = asyncio.run(tool.execute(_call("clear")))
    assert "cleared" in cleared.content


def test_goal_policy_allows_tool(tmp_path: Path) -> None:
    assert WorkspacePolicy(tmp_path).check(_call("get")).decision == "allow"


def test_goal_activation_does_not_survive_restart_or_external_edits(
    tmp_path: Path,
) -> None:
    store = GoalStore(tmp_path)
    goal = store.set("ship backend")
    assert store.runnable() == goal
    reopened = GoalStore(tmp_path)
    assert reopened.load() == goal
    assert reopened.runnable() is None
    assert reopened.resume() is not None
    assert reopened.runnable() is not None
    assert store.runnable() is None
    assert reopened.pause().status == "paused"
    assert reopened.runnable() is None
    assert reopened.resume().status == "active"
    assert (
        reopened.block("missing deployment credentials").blocked_reason
        == "missing deployment credentials"
    )
    assert reopened.runnable() is None
    reopened.resume()
    reopened.complete()
    assert reopened.runnable() is None


def test_goal_tools_reject_automatic_pause_resume_and_stale_completion(
    tmp_path: Path,
) -> None:
    store = GoalStore(tmp_path)
    old = store.set("full objective")
    tool = GoalTool(tmp_path, store=store, allow_control=lambda: False)
    for action in ("pause", "resume"):
        result = asyncio.run(tool.execute(_call(action, "smaller objective")))
        assert result.is_error
        assert store.load() == old
    newer = store.set("updated full objective")
    stale = asyncio.run(tool.execute(_call("complete", updated_at=old.updated_at)))
    assert stale.is_error
    assert store.load() == newer
    completed = asyncio.run(
        tool.execute(_call("complete", updated_at=newer.updated_at))
    )
    assert not completed.is_error
    assert store.load().status == "done"


def test_goal_tools_can_create_update_and_clear_in_automatic_rounds(
    tmp_path: Path,
) -> None:
    store = GoalStore(tmp_path)
    tool = GoalTool(tmp_path, store=store, allow_control=lambda: False)
    created = asyncio.run(tool.execute(_call("set", "implement backend")))
    assert not created.is_error
    assert store.runnable().objective == "implement backend"
    updated = asyncio.run(
        tool.execute(_call("set", "implement backend and verify migrations"))
    )
    assert not updated.is_error
    assert store.runnable().objective == "implement backend and verify migrations"
    cleared = asyncio.run(tool.execute(_call("clear")))
    assert not cleared.is_error
    assert store.load() is None
    assert store.runnable() is None
