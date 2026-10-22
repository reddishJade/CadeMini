"""Cade 文件快照与 undo 的回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from cade.coding.tools.bash import BashTool
from cade.coding.tools.write import WriteTool
from cade.coding.workspace.snapshots import SnapshotConflict, SnapshotStore
from cade.harness.session.types import ToolCallBlock


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


def test_snapshot_writers_refresh_after_append_and_atomic_undo(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("original", encoding="utf-8")
    first = SnapshotStore(tmp_path)
    second = SnapshotStore(tmp_path)
    assert second.list() == []
    with first.change(target):
        target.write_text("first", encoding="utf-8")
    assert len(second.list()) == 1
    with second.change(target):
        target.write_text("second", encoding="utf-8")
    assert len(first.list()) == 2
    second.undo_last()
    assert len(first.list()) == 1
    first.undo_last()
    assert second.list() == []
    assert target.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("reopen", [False, True])
def test_snapshot_index_repairs_torn_tail_before_next_capture(
    tmp_path: Path, reopen: bool
) -> None:
    target = tmp_path / "a.txt"
    target.write_text("original", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    with store.change(target):
        target.write_text("first", encoding="utf-8")
    committed = store.index.read_bytes()
    with store.index.open("ab") as stream:
        stream.write(b'{"id":"incomplete')
    if reopen:
        store = SnapshotStore(tmp_path)
    assert len(store.list()) == 1
    with store.change(target):
        target.write_text("second", encoding="utf-8")
    assert store.index.read_bytes().startswith(committed)
    assert b"incomplete" not in store.index.read_bytes()
    assert len(SnapshotStore(tmp_path).list()) == 2
    store.undo_last()
    store.undo_last()
    assert target.read_text(encoding="utf-8") == "original"


def test_snapshot_index_rejects_committed_corruption_before_append(
    tmp_path: Path,
) -> None:
    target = tmp_path / "a.txt"
    store = SnapshotStore(tmp_path)
    with store.change(target):
        target.write_text("first", encoding="utf-8")
    with store.index.open("ab") as stream:
        stream.write(b"invalid record\n")
    corrupted = store.index.read_bytes()
    with pytest.raises(json.JSONDecodeError):
        store.capture(target)
    assert store.index.read_bytes() == corrupted
    assert target.read_text(encoding="utf-8") == "first"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory durability")
def test_each_capture_syncs_the_snapshot_root_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SnapshotStore(tmp_path)
    root = store.root.stat()
    syncs: list[int] = []
    original = os.fsync

    def sync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == (root.st_dev, root.st_ino):
            syncs.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(os, "fsync", sync)
    for _ in range(2):
        snapshot_id = store.capture(tmp_path / "new.txt")
        store.complete(snapshot_id)
    assert len(syncs) == 2


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
