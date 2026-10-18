"""Cade Mini 文件快照与 undo 的回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cade.mini import BashTool, SnapshotStore, ToolCallBlock, WriteTool


def _call(path: str, content: str) -> ToolCallBlock:
    return ToolCallBlock(
        id="s1",
        name="write",
        args={"path": path, "content": content},
    )


def test_capture_and_undo_restores_content(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)

    store.capture(target)
    target.write_text("new", encoding="utf-8")
    snapshot = store.undo_last()

    assert snapshot is not None
    assert snapshot.path == "a.txt"
    assert target.read_text(encoding="utf-8") == "old"
    assert store.undo_last() is None


def test_undo_created_file_deletes_it(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    store = SnapshotStore(tmp_path)

    store.capture(target)
    target.write_text("content", encoding="utf-8")
    store.undo_last()

    assert not target.exists()


def test_write_tool_snapshots_before_change(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    tool = WriteTool(tmp_path, store)

    asyncio.run(
        tool.execute(
            _call("a.txt", "new"),
            None,
        )
    )

    assert target.read_text(encoding="utf-8") == "new"
    store.undo_last()
    assert target.read_text(encoding="utf-8") == "old"


def test_snapshot_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)

    with pytest.raises(ValueError):
        store.capture(tmp_path / ".." / "outside.txt")


def test_bash_rm_snapshots_before_change(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    tool = BashTool(tmp_path, snapshots=store)

    asyncio.run(
        tool.execute(
            ToolCallBlock(id="b1", name="bash", args={"command": "rm a.txt"}),
            None,
        )
    )

    assert not target.exists()
    store.undo_last()
    assert target.read_text(encoding="utf-8") == "old"
