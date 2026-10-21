"""逐步撤销、代码恢复与待完成事务的回归测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cade.coding.workspace.recovery.coordinator import WorkspaceRecovery
from cade.coding.workspace.recovery.store import Pending, Scope
from cade.coding.workspace.snapshots import FileState, SnapshotConflict, SnapshotStore
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import EventType, JsonValue, LogEntry, UserMessage


def _setup(root: Path) -> tuple[SessionLog, SnapshotStore, WorkspaceRecovery]:
    log = SessionLog.create(root / "sessions")
    log.append("system", {"text": "sys"})
    snapshots = SnapshotStore(root)
    return log, snapshots, WorkspaceRecovery(snapshots)


def _turn(
    log: SessionLog,
    snapshots: SnapshotStore,
    recovery: WorkspaceRecovery,
    text: str,
    files: dict[str, str | None],
) -> str:
    with log.run_lock(), recovery.record(log):
        user = log.append("user", {"text": text})
        recovery.started(log, user)
        for path, content in files.items():
            target = snapshots.workspace / path
            with snapshots.change(target):
                if content is None:
                    target.unlink()
                else:
                    target.write_text(content, encoding="utf-8")
        log.append("assistant", {"blocks": [{"type": "text", "text": "done"}]})
    return user.id


def _users(log: SessionLog) -> list[str]:
    return [m.text for m in project(log.branch()) if isinstance(m, UserMessage)]


def test_undo_redo_is_stepwise_and_survives_restart(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("zero", encoding="utf-8")
    _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    _turn(log, snapshots, recovery, "two", {"a.txt": "two"})
    recovery.step(log, "undo")
    assert target.read_text() == "one"
    assert _users(log) == ["one"]
    assert log.navigation_draft == "two"
    recovery.step(log, "undo")
    assert target.read_text() == "zero"
    assert _users(log) == []
    reopened = SessionLog.open(log.path)
    recovery = WorkspaceRecovery(SnapshotStore(tmp_path))
    recovery.step(reopened, "redo")
    assert target.read_text() == "one"
    assert _users(reopened) == ["one"]
    recovery.step(reopened, "redo")
    assert target.read_text() == "two"
    assert _users(reopened) == ["one", "two"]
    assert "Nothing" in recovery.step(reopened, "redo")


@pytest.mark.parametrize("scope", ["conversation", "code", "both"])
def test_rewind_scopes_and_undo_rewind(tmp_path: Path, scope: Scope) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    first = _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    _turn(log, snapshots, recovery, "two", {"a.txt": "two"})
    recovery.restore(log, first, scope=scope)
    assert _users(log) == (["one", "two"] if scope == "code" else [])
    target = tmp_path / "a.txt"
    assert (target.read_text() if target.exists() else None) == (
        "two" if scope == "conversation" else None
    )
    recovery.step(log, "undo")
    assert target.read_text() == "two"
    assert _users(log) == ["one", "two"]


def test_conflict_preflights_all_files_and_preserves_history(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "change", {"a.txt": "agent", "b.txt": "agent"})
    (tmp_path / "b.txt").write_text("human", encoding="utf-8")
    original = log.path.read_bytes()
    with pytest.raises(SnapshotConflict, match="changed"):
        recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "agent"
    assert (tmp_path / "b.txt").read_text() == "human"
    assert log.path.read_bytes() == original
    assert not recovery.store.pending_path.exists()


def test_recovery_resumes_after_second_file_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "change", {"a.txt": "agent", "b.txt": "agent"})
    original = snapshots.apply_state
    calls = 0

    def fail(path: str, expected: FileState | None, desired: FileState | None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        original(path, expected, desired)

    with monkeypatch.context() as patch:
        patch.setattr(snapshots, "apply_state", fail)
        with pytest.raises(OSError, match="simulated"):
            recovery.step(log, "undo")
    assert recovery.store.pending_path.exists()
    assert _users(log) == ["change"]
    with (
        pytest.raises(SnapshotConflict, match="pending"),
        snapshots.change(tmp_path / "other.txt"),
    ):
        pass
    reopened = SessionLog.open(log.path)
    assert WorkspaceRecovery(SnapshotStore(tmp_path)).recover(reopened)
    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()
    assert _users(reopened) == []
    recovery.step(reopened, "redo")
    assert (tmp_path / "a.txt").read_text() == "agent"
    assert (tmp_path / "b.txt").read_text() == "agent"


def test_revert_selected_file_preserves_conversation_and_other_files(
    tmp_path: Path,
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    user = _turn(log, snapshots, recovery, "change", {"a.txt": "a", "b.txt": "b"})
    recovery.restore(log, user, scope="code", kind="revert", paths=["a.txt"])
    assert _users(log) == ["change"]
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "b"
    assert recovery.request_context(log)
    recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "a"


def test_rewind_accounts_for_revert_and_later_turns(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    first = _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    second = _turn(log, snapshots, recovery, "two", {"a.txt": "two"})
    recovery.restore(log, second, scope="code", kind="revert")
    _turn(log, snapshots, recovery, "three", {"a.txt": "three"})
    recovery.restore(log, first, scope="both")
    assert not (tmp_path / "a.txt").exists()
    assert _users(log) == []
    recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "three"
    assert _users(log) == ["one", "two", "three"]


def test_tree_does_not_restore_files_but_rewind_can_restore_its_checkpoint(
    tmp_path: Path,
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    first = _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    first_head = log.head_id
    _turn(log, snapshots, recovery, "two", {"a.txt": "two"})
    assert first_head is not None
    log.switch_branch(first_head)
    assert (tmp_path / "a.txt").read_text() == "two"
    recovery.restore(log, first, scope="both")
    assert not (tmp_path / "a.txt").exists()
    recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "two"
    assert _users(log) == ["one"]


def test_bounded_revert_preserves_later_independent_changes(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    first = _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    second = _turn(log, snapshots, recovery, "two", {"b.txt": "two"})
    _turn(log, snapshots, recovery, "three", {"c.txt": "three"})
    recovery.restore(log, first, end_id=second, scope="code", kind="revert")
    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()
    assert (tmp_path / "c.txt").read_text() == "three"
    assert _users(log) == ["one", "two", "three"]


def test_file_selection_does_not_fail_on_unselected_manual_edit(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    first = _turn(log, snapshots, recovery, "one", {"a.txt": "one", "b.txt": "one"})
    (tmp_path / "b.txt").write_text("human", encoding="utf-8")
    _turn(log, snapshots, recovery, "two", {"b.txt": "two"})
    recovery.restore(log, first, scope="code", kind="revert", paths=["a.txt"])
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text() == "two"


@pytest.mark.parametrize("overlap", [True, False])
def test_other_session_changes_are_not_overwritten(
    tmp_path: Path, overlap: bool
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    other = SessionLog.create(tmp_path / "sessions")
    other.append("system", {"text": "sys"})
    _turn(
        other, snapshots, recovery, "other", {"a.txt" if overlap else "b.txt": "other"}
    )
    if overlap:
        with pytest.raises(SnapshotConflict, match="changed"):
            recovery.step(log, "undo")
        assert (tmp_path / "a.txt").read_text() == "other"
    else:
        recovery.step(log, "undo")
        assert not (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").read_text() == "other"


def test_recovery_after_log_commit_deduplicates_action_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "one", {"a.txt": "one"})

    def fail(pending: Pending) -> None:
        recovery.store.append(pending.session_id, pending.event)
        raise OSError("simulated acknowledgement failure")

    with monkeypatch.context() as patch:
        patch.setattr(recovery.store, "finish", fail)
        with pytest.raises(OSError, match="acknowledgement"):
            recovery.step(log, "undo")
    reopened = SessionLog.open(log.path)
    recovery.recover(reopened)
    assert (
        len([e for e in recovery.store.events(log.session_id) if e.kind == "undo"]) == 1
    )
    assert len([e for e in reopened.entries() if e.type == "restore"]) == 1
    recovery.step(reopened, "redo")
    assert (tmp_path / "a.txt").read_text() == "one"


def test_corrupt_blob_blocks_restore_before_any_file_changes(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "one", {"a.txt": "one", "b.txt": "two"})
    action = recovery.store.history(log.session_id).undo[-1]
    state = action.changes[0].after
    assert state is not None
    (snapshots.root / "blobs" / state.sha256).write_bytes(b"corrupt")
    with pytest.raises(SnapshotConflict, match="corrupted"):
        recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "one"
    assert (tmp_path / "b.txt").read_text() == "two"
    assert not recovery.store.pending_path.exists()


def test_workspace_lock_rejects_competing_session(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    with (
        SnapshotStore(tmp_path).workspace_lock(),
        pytest.raises(SnapshotConflict, match="busy"),
    ):
        recovery.step(log, "undo")
    assert (tmp_path / "a.txt").read_text() == "one"


def test_incomplete_file_outcome_allows_only_conversation_restore(
    tmp_path: Path,
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    target = tmp_path / "a.txt"
    with log.run_lock(), recovery.record(log):
        user = log.append("user", {"text": "interrupted"})
        recovery.started(log, user)
        snapshots.capture(target)
        target.write_text("unknown outcome", encoding="utf-8")
    assert not recovery.store.history(log.session_id).undo[-1].complete
    with pytest.raises(SnapshotConflict, match="unknown file outcome"):
        recovery.step(log, "undo")
    recovery.restore(log, user.id, scope="conversation")
    assert _users(log) == []
    assert target.read_text() == "unknown outcome"


def test_pending_recovery_preserves_a_third_external_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    _turn(log, snapshots, recovery, "one", {"a.txt": "one", "b.txt": "two"})
    original = snapshots.apply_state
    applied: list[str] = []

    def fail(path: str, expected: FileState | None, desired: FileState | None) -> None:
        if applied:
            raise OSError("simulated crash")
        original(path, expected, desired)
        applied.append(path)

    with monkeypatch.context() as patch:
        patch.setattr(snapshots, "apply_state", fail)
        with pytest.raises(OSError, match="crash"):
            recovery.step(log, "undo")
    changed = tmp_path / applied[0]
    changed.write_text("human", encoding="utf-8")
    before = log.path.read_bytes()
    with pytest.raises(SnapshotConflict, match="changed"):
        WorkspaceRecovery(SnapshotStore(tmp_path)).recover(SessionLog.open(log.path))
    assert changed.read_text() == "human"
    assert log.path.read_bytes() == before
    assert recovery.store.pending_path.exists()


def test_root_recovery_keeps_saved_system_prompt_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cade.app.bootstrap import open_log

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "session-legacy.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "legacy-user",
                "parent": None,
                "ts": "2020-01-01T00:00:00Z",
                "type": "user",
                "data": {"text": "legacy"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    log = SessionLog.open(path)
    recovery = WorkspaceRecovery(SnapshotStore(tmp_path), system_prompt="saved prompt")
    original = log.append

    def fail(kind: EventType, data: dict[str, JsonValue]) -> LogEntry:
        if kind == "system":
            raise OSError("simulated system write failure")
        return original(kind, data)

    with monkeypatch.context() as patch:
        patch.setattr(log, "append", fail)
        with pytest.raises(OSError, match="system write"):
            recovery.restore(log, "legacy-user", scope="conversation")
    reopened = open_log(
        sessions,
        "legacy",
        "changed configuration",
        tmp_path,
        recovery=WorkspaceRecovery(
            SnapshotStore(tmp_path), system_prompt="changed configuration"
        ),
    )
    assert _users(reopened) == []
    assert project(reopened.branch())[0].text == "saved prompt"
    assert reopened.navigation_draft == "legacy"
    new_recovery = WorkspaceRecovery(
        SnapshotStore(tmp_path), system_prompt="saved prompt"
    )
    new_recovery.step(reopened, "undo")
    assert _users(reopened) == ["legacy"]
    new_recovery.step(reopened, "redo")
    assert _users(reopened) == []


def test_hard_link_is_rejected_before_mutation(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("human", encoding="utf-8")
    try:
        os.link(target, tmp_path / "alias.txt")
    except OSError as exc:
        pytest.skip(f"Hard links unavailable: {exc}")
    with pytest.raises(SnapshotConflict), log.run_lock(), recovery.record(log):
        user = log.append("user", {"text": "edit"})
        recovery.started(log, user)
        with snapshots.change(target):
            target.write_text("agent", encoding="utf-8")
    assert target.read_text() == "human"
    assert (tmp_path / "alias.txt").read_text() == "human"


def test_read_only_recovery_allows_conversation_but_not_code(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    user = _turn(log, snapshots, recovery, "one", {"a.txt": "one"})
    readonly = WorkspaceRecovery(snapshots, read_only=True)
    before = log.path.read_bytes()
    with pytest.raises(SnapshotConflict, match="read-only"):
        readonly.step(log, "undo")
    assert log.path.read_bytes() == before
    assert not readonly.store.pending_path.exists()
    readonly.restore(log, user, scope="conversation")
    assert _users(log) == []
    assert (tmp_path / "a.txt").read_text() == "one"


def test_new_turn_invalidates_redo_without_deleting_old_facts(tmp_path: Path) -> None:
    log, snapshots, recovery = _setup(tmp_path)
    old = _turn(log, snapshots, recovery, "old", {"a.txt": "old"})
    recovery.step(log, "undo")
    _turn(log, snapshots, recovery, "new", {"a.txt": "new"})
    assert "Nothing" in recovery.step(log, "redo")
    assert old in {e.id for e in log.entries()}
    assert _users(log) == ["new"]
