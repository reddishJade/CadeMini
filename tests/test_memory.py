"""Cade 持久记忆的回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cade.coding.context.memory import MemoryTool, load_memory
from cade.coding.security.policy import WorkspacePolicy
from cade.harness.session.types import ToolCallBlock


def _call(text: str) -> ToolCallBlock:
    return ToolCallBlock(id="m1", name="memory", args={"text": text})


def test_load_memory(tmp_path: Path) -> None:
    (tmp_path / "MEMORY.md").write_text("- prefer small commits", encoding="utf-8")

    memory = load_memory(tmp_path)

    assert memory is not None
    assert "prefer small commits" in memory
    assert 'source="MEMORY.md"' in memory


def test_missing_or_empty_memory(tmp_path: Path) -> None:
    assert load_memory(tmp_path) is None
    (tmp_path / "MEMORY.md").write_text("  \n", encoding="utf-8")
    assert load_memory(tmp_path) is None


def test_memory_tool_appends_facts(tmp_path: Path) -> None:
    tool = MemoryTool(tmp_path)

    asyncio.run(tool.execute(_call("first fact")))
    asyncio.run(tool.execute(_call("second fact")))

    text = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert text.count("# Memory") == 1
    assert "- first fact" in text
    assert "- second fact" in text


def test_memory_tool_rejects_empty_text(tmp_path: Path) -> None:
    tool = MemoryTool(tmp_path)

    with pytest.raises(ValueError):
        asyncio.run(tool.execute(_call("  ")))


def test_memory_policy(tmp_path: Path) -> None:
    call = _call("fact")
    assert WorkspacePolicy(tmp_path).check(call).decision == "allow"
    assert WorkspacePolicy(tmp_path, read_only=True).check(call).decision == "deny"
