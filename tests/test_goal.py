"""Cade 目标存储与工具的回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from cade.core.types import JsonValue, ToolCallBlock
from cade.extensions.goal import GoalStore, GoalTool
from cade.safety.policy import WorkspacePolicy


def _call(action: str, objective: str | None = None) -> ToolCallBlock:
    args: dict[str, JsonValue] = {"action": action}
    if objective is not None:
        args["objective"] = objective
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

    done = asyncio.run(tool.execute(_call("complete")))
    assert "completed" in done.content

    cleared = asyncio.run(tool.execute(_call("clear")))
    assert "cleared" in cleared.content


def test_goal_policy_allows_tool(tmp_path: Path) -> None:
    assert WorkspacePolicy(tmp_path).check(_call("get")).decision == "allow"
