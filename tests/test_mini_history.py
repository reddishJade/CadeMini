"""Cade Mini history 工具的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.core.log import SessionLog
from cade_mini.core.projection import project
from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.safety.policy import WorkspacePolicy
from cade_mini.tools.history import HistoryTool


def _call(args: dict[str, JsonValue]) -> ToolCallBlock:
    return ToolCallBlock(id="h1", name="history", args=args)


def _log(tmp_path: Path) -> SessionLog:
    return SessionLog.create(tmp_path / "sessions", session_id="h1")


async def test_history_search_and_exact_lookup(tmp_path: Path) -> None:
    log = _log(tmp_path)
    user = log.append("user", {"text": "needle task", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "text", "text": "answer"}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )
    log.append(
        "tool_result",
        {
            "call_id": "c1",
            "status": "ok",
            "content": "exact evidence " * 100,
        },
    )
    tool = HistoryTool(tmp_path, log)

    search = await tool.execute(_call({"query": "needle"}))
    assert "needle task" in search.content
    assert user.id in search.content

    exact = await tool.execute(_call({"entry_id": user.id}))
    assert "needle task" in exact.content

    call = await tool.execute(_call({"call_id": "c1"}))
    assert "exact evidence" in call.content

    missing = await tool.execute(_call({"query": "not-there"}))
    assert "No matching" in missing.content


async def test_history_rejects_missing_or_empty_selectors(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "task", "source": "user"})
    tool = HistoryTool(tmp_path, log)

    with pytest.raises(ValueError):
        await tool.execute(_call({"entry_id": "missing"}))
    with pytest.raises(ValueError):
        await tool.execute(_call({"call_id": "missing"}))
    with pytest.raises(ValueError):
        await tool.execute(_call({"query": "  "}))


async def test_history_finds_compacted_evidence(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append("user", {"text": "old task", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [{"type": "tool_call", "id": "c1", "name": "bash", "args": {}}],
            "model": "m",
            "provider": "p",
            "usage": None,
            "provider_state": None,
        },
    )
    log.append(
        "tool_result",
        {"call_id": "c1", "status": "ok", "content": "shadowed evidence"},
    )
    new_task = log.append("user", {"text": "new task", "source": "user"})
    log.append(
        "compaction",
        {
            "summary": "summary",
            "first_kept_id": new_task.id,
            "tokens_before": 100,
            "model": "m",
            "provider": "p",
            "usage": None,
            "details": None,
        },
    )
    tool = HistoryTool(tmp_path, log)

    assert not any(
        getattr(message, "call_id", "") == "c1" for message in project(log.branch())
    )
    result = await tool.execute(_call({"call_id": "c1"}))

    assert "shadowed evidence" in result.content


def test_policy_allows_history(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path)

    assert policy.check(_call({"query": "anything"})) == "allow"
