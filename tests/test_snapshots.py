"""Cade 文件快照与 undo 的回归测试。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from cade.core.types import ToolCallBlock
from cade.extensions.snapshots import SnapshotConflict, SnapshotStore
from cade.tools.workspace import BashTool, WriteTool


def _call(path: str, content: str) -> ToolCallBlock:
    return ToolCallBlock(
        id="s1",
        name="write",
        args={"path": path, "content": content},
    )


def _symlink_or_skip(link: Path, target: Path) -> None:
    """创建符号链接；平台不允许时跳过该用例。"""
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")


def test_capture_and_undo_restores_content(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)

    with store.change(target):
        target.write_text("new", encoding="utf-8")
    snapshot = store.undo_last()

    assert snapshot is not None
    assert snapshot.path == "a.txt"
    assert target.read_text(encoding="utf-8") == "old"
    assert store.undo_last() is None


def test_undo_created_file_deletes_it(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    store = SnapshotStore(tmp_path)

    with store.change(target):
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


@pytest.mark.parametrize("change", ["edit", "delete", "directory", "symlink"])
def test_undo_rejects_changed_target_without_consuming_snapshot(
    tmp_path: Path, change: str
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    with store.change(target):
        target.write_text("agent", encoding="utf-8")
    if change == "edit":
        target.write_text("human", encoding="utf-8")
    else:
        target.unlink()
        if change == "directory":
            target.mkdir()
        elif change == "symlink":
            other = tmp_path / "other.txt"
            other.write_text("human", encoding="utf-8")
            _symlink_or_skip(target, other)
    before_index = store.index.read_bytes()

    with pytest.raises(SnapshotConflict):
        store.undo_last()

    assert store.index.read_bytes() == before_index
    if change == "edit":
        assert target.read_text(encoding="utf-8") == "human"
    elif change == "symlink":
        assert target.is_symlink()
        assert target.read_text(encoding="utf-8") == "human"
    elif change == "directory":
        assert target.is_dir()
    else:
        assert not target.exists()


def test_undo_rejects_corrupt_payload_before_changing_file(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("old", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    with store.change(target):
        target.write_text("new", encoding="utf-8")
    next((store.root / "blobs").iterdir()).write_bytes(b"corrupted")

    with pytest.raises(SnapshotConflict, match="corrupted"):
        store.undo_last()
    assert target.read_text(encoding="utf-8") == "new"
    assert len(store.list()) == 1


def test_undo_requires_completion(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    store = SnapshotStore(tmp_path)
    snapshot_id = store.capture(target)
    target.write_text("unknown outcome", encoding="utf-8")
    with pytest.raises(SnapshotConflict, match="unknown"):
        store.undo_last()
    store.complete(snapshot_id)
    with pytest.raises(SnapshotConflict, match="already complete"):
        store.complete(snapshot_id)
    store.undo_last()
    assert not target.exists()


def test_repeated_undo_preserves_modes_and_survives_reopen(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("original", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o755)
    store = SnapshotStore(tmp_path)
    for content in ("first", "second"):
        with store.change(target):
            target.write_text(content, encoding="utf-8")
    reopened = SnapshotStore(tmp_path)
    reopened.undo_last()
    assert target.read_text(encoding="utf-8") == "first"
    reopened.undo_last()
    assert target.read_text(encoding="utf-8") == "original"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o755


def test_snapshot_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)

    with pytest.raises(ValueError):
        store.capture(tmp_path / ".." / "outside.txt")


def test_snapshot_retains_both_content_images_and_nested_shell_targets(
    tmp_path: Path,
) -> None:
    store = SnapshotStore(tmp_path)
    source = tmp_path / "old name.txt"
    destination = tmp_path / "new name.txt"
    source.write_text("original", encoding="utf-8")
    with store.workspace_lock(), store.change(source), store.change(destination):
        source.rename(destination)
    records = [store.captured_change(snapshot.id) for snapshot in store.list()]
    deleted = next(record for record in records if record[0] == "old name.txt")
    created = next(record for record in records if record[0] == "new name.txt")
    assert deleted[1] is not None and deleted[2] is None
    assert created[1] is None and created[2] is not None
    assert store.verify_payload(created[2]) == b"original"
    assert deleted[1] == created[2]


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
