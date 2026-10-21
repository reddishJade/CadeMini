"""协调轮次检查点、逐步撤销和跨文件恢复，不操作终端。"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal
from uuid import uuid4

from cade.core.log import SessionLog
from cade.core.types import JsonValue, LogEntry
from cade.extensions.recovery import (
    Action,
    FileChange,
    HistoryEvent,
    Pending,
    RecoveryKind,
    RecoveryStore,
    RestoreNotice,
    Scope,
    Turn,
)
from cade.extensions.snapshots import SnapshotConflict, SnapshotStore

type LiteralStep = Literal["undo", "redo"]
type LiteralRestore = Literal["rewind", "revert"]

_ROOT_TURN: ContextVar[Turn | None] = ContextVar("recovery_root_turn", default=None)


def _draft(log: SessionLog) -> str | None:
    entries = log.entries()
    by_id = {entry.id: entry for entry in entries}
    for entry in reversed(log.branch()):
        if entry.type in {"user", "assistant"}:
            return None
        if entry.type == "restore":
            value = entry.data["draft_id"]
            return value if isinstance(value, str) else None
        if entry.type == "branch":
            selected = by_id[str(entry.data["selected_id"])]
            return selected.id if selected.type == "user" else None
    return None


def _combine(changes: Sequence[FileChange]) -> list[FileChange]:
    combined: dict[str, FileChange] = {}
    aliases: dict[str, str] = {}
    for change in changes:
        canonical = os.path.normcase(change.path)
        if canonical in aliases and aliases[canonical] != change.path:
            raise SnapshotConflict("Recovery contains case-aliased file paths")
        aliases[canonical] = change.path
        previous = combined.get(change.path)
        if previous is not None:
            if previous.after != change.before:
                raise SnapshotConflict(f"Untracked edit between changes: {change.path}")
            change = change.model_copy(update={"before": previous.before})
        combined[change.path] = change
    return [change for change in combined.values() if change.before != change.after]


class WorkspaceRecovery:
    """会话锁由调用方先取得；工作区锁覆盖捕获、执行和恢复。"""

    def __init__(
        self,
        snapshots: SnapshotStore,
        *,
        system_prompt: str | None = None,
        read_only: bool = False,
    ) -> None:
        self.snapshots = snapshots
        self.system_prompt = system_prompt
        self.read_only = read_only
        self.store = RecoveryStore(snapshots.workspace)

    @contextmanager
    def record(self, log: SessionLog) -> Iterator[None]:
        parent = _ROOT_TURN.get()
        if parent is not None and parent.workspace == str(self.snapshots.workspace):
            yield
            return
        with self.snapshots.workspace_lock(recovery=True):
            log.entries()
            self._recover(log)
            self._recover_turns(log)
            self._align(log)
            turn = Turn(
                id=uuid4().hex,
                session_id=log.session_id,
                log_path=str(log.path),
                before_head=log.head_id,
                before_draft=_draft(log),
                created=time.time_ns(),
                workspace=str(self.snapshots.workspace),
            )
            self.store.save_turn(turn)
            token = _ROOT_TURN.set(turn)
            try:
                with self.snapshots.recording(
                    self.store.turn_directory(turn.id) / "captures.jsonl"
                ):
                    yield
            finally:
                _ROOT_TURN.reset(token)
                self._seal(log, turn)

    def started(self, log: SessionLog, user: LogEntry) -> None:
        turn = _ROOT_TURN.get()
        if turn is None or turn.log_path != str(log.path):
            return
        turn.user_id = user.id
        self.store.save_turn(turn)
        log.append(
            "checkpoint",
            {
                "checkpoint_id": turn.id,
                "user_id": user.id,
                "anchor_id": turn.before_head,
                "phase": "started",
            },
        )

    def _seal(self, log: SessionLog, turn: Turn) -> None:
        if turn.user_id is None or turn.user_id not in {e.id for e in log.branch()}:
            self.store.seal(turn.id)
            return
        started = next(
            (
                e
                for e in log.entries()
                if e.type == "checkpoint"
                and e.data["checkpoint_id"] == turn.id
                and e.data["phase"] == "started"
            ),
            None,
        )
        if started is None:
            if (self.store.turn_directory(turn.id) / "captures.jsonl").exists():
                raise SnapshotConflict(
                    "Checkpoint linkage is missing; inspect recovery data"
                )
            self.store.seal(turn.id)
            return
        if (
            started.data["user_id"] != turn.user_id
            or started.data["anchor_id"] != turn.before_head
        ):
            raise SnapshotConflict(
                "Checkpoint metadata does not match the session fact"
            )
        if any(a.id == turn.id for a in self.store.actions(log.session_id)):
            self.store.seal(turn.id)
            return
        changes: list[FileChange] = []
        warnings: list[str] = []
        capture_file = self.store.turn_directory(turn.id) / "captures.jsonl"
        if capture_file.exists():
            for snapshot_id in capture_file.read_text(encoding="utf-8").splitlines():
                try:
                    path, before, after = self.snapshots.captured_change(snapshot_id)
                    changes.append(FileChange(path=path, before=before, after=after))
                except (OSError, ValueError) as exc:
                    warnings.append(str(exc))
        try:
            changes = _combine(changes)
            for change in changes:
                self.snapshots.verify_state(change.path, change.after)
        except (OSError, ValueError) as exc:
            warnings.append(str(exc))
        phase = "incomplete" if warnings else "completed"
        completed = next(
            (
                e
                for e in log.entries()
                if e.type == "checkpoint"
                and e.data["checkpoint_id"] == turn.id
                and e.data["phase"] != "started"
            ),
            None,
        )
        if completed is None:
            log.append(
                "checkpoint",
                {
                    "checkpoint_id": turn.id,
                    "user_id": turn.user_id,
                    "anchor_id": turn.before_head,
                    "phase": phase,
                },
            )
        action = Action(
            id=turn.id,
            kind="turn",
            before_head=turn.before_head,
            after_head=log.head_id,
            before_draft=turn.before_draft,
            user_id=turn.user_id,
            changes=changes,
            complete=not warnings,
            warnings=warnings,
        )
        self.store.append(
            log.session_id,
            HistoryEvent(
                id=turn.id,
                kind="action",
                action=action,
                head_id=log.head_id,
            ),
        )
        self.store.seal(turn.id)

    def _recover_turns(self, log: SessionLog) -> None:
        for turn in self.store.open_turns(log.session_id):
            if turn.log_path != str(log.path):
                raise SnapshotConflict("Checkpoint belongs to a different session path")
            self._seal(log, turn)

    def _align(self, log: SessionLog) -> None:
        history = self.store.history(log.session_id)
        if history.head_id != log.head_id and (history.undo or history.redo):
            self.store.append(
                log.session_id,
                HistoryEvent(
                    id=uuid4().hex,
                    kind="reset",
                    head_id=log.head_id,
                ),
            )

    def prepare_run(self, log: SessionLog) -> None:
        """交互宿主先处理未完成恢复，避免新输入使用过期的转录。"""
        if self.store.pending_path.exists():
            raise SnapshotConflict(
                "Recovery is pending; resume the session or run /undo before starting new work"
            )
        self.recover(log)

    def recover(self, log: SessionLog) -> bool:
        if not self.store.pending_path.exists() and not self.store.open_turns(
            log.session_id
        ):
            return False
        with log.run_lock(), self.snapshots.workspace_lock(recovery=True):
            log.entries()
            recovered = self._recover(log)
            self._recover_turns(log)
            return recovered

    def _recover(self, log: SessionLog) -> bool:
        pending = self.store.pending()
        if pending is None:
            return False
        if pending.session_id != log.session_id or pending.log_path != str(log.path):
            raise SnapshotConflict(
                f"Recovery is pending for session {pending.session_id}; resume that session first"
            )
        self._finish(log, pending)
        return True

    def _validate(
        self, changes: Sequence[FileChange], *, resuming: bool = False
    ) -> None:
        for change in changes:
            self.snapshots.verify_payload(change.before)
            self.snapshots.verify_payload(change.after)
            current = self.snapshots.capture_state(change.path)
            allowed = (change.before, change.after) if resuming else (change.before,)
            if current not in allowed:
                raise SnapshotConflict(f"File changed since checkpoint: {change.path}")

    def _finish(self, log: SessionLog, pending: Pending) -> None:
        if self.read_only and pending.changes:
            raise SnapshotConflict(
                "Code recovery is disabled in read-only mode; resume without --read-only to finish"
            )
        entries = log.entries()
        committed = next(
            (
                e
                for e in entries
                if e.type == "restore" and e.data["operation_id"] == pending.id
            ),
            None,
        )
        if committed is None and log.head_id != pending.expected_head:
            raise SnapshotConflict("Conversation changed during pending recovery")
        by_id = {entry.id: entry for entry in entries}
        if pending.target_head is not None and pending.target_head not in by_id:
            raise SnapshotConflict(
                "Pending restore references an unknown conversation target"
            )
        if pending.draft_id is not None and (
            pending.draft_id not in by_id or by_id[pending.draft_id].type != "user"
        ):
            raise SnapshotConflict("Pending restore references an unknown user input")
        data: dict[str, JsonValue] = {
            "operation_id": pending.id,
            "target_id": pending.target_head,
            "draft_id": pending.draft_id,
            "scope": pending.scope,
            "kind": pending.kind,
        }
        if committed is not None and committed.data != data:
            raise SnapshotConflict("Committed restore does not match its pending plan")
        self._validate(pending.changes, resuming=True)
        for change in pending.changes:
            current = self.snapshots.capture_state(change.path)
            if current != change.after:
                self.snapshots.apply_state(change.path, change.before, change.after)
        if committed is None:
            committed = log.append("restore", data)
        if pending.system_prompt is not None and not any(
            e.type == "system" for e in log.branch()
        ):
            log.append("system", {"text": pending.system_prompt})
        pending.event.head_id = log.head_id
        if pending.event.action is not None:
            pending.event.action.after_head = log.head_id
        self.store.finish(pending)

    def _prepare(
        self,
        log: SessionLog,
        *,
        kind: RecoveryKind,
        scope: Scope,
        target: str | None,
        draft: str | None,
        changes: list[FileChange],
        event: HistoryEvent,
    ) -> None:
        by_id = {e.id: e for e in log.entries()}
        if target is not None and target not in by_id:
            raise SnapshotConflict("Unknown conversation restore target")
        if draft is not None and (draft not in by_id or by_id[draft].type != "user"):
            raise SnapshotConflict("Unknown historical input")
        if self.read_only and changes:
            raise SnapshotConflict(
                "Code recovery is disabled in read-only mode; choose conversation-only rewind"
            )
        self._validate(changes)
        pending = Pending(
            id=event.id,
            session_id=log.session_id,
            log_path=str(log.path),
            expected_head=log.head_id,
            target_head=target,
            draft_id=draft,
            kind=kind,
            scope=scope,
            changes=changes,
            event=event,
            system_prompt=self.system_prompt
            if target is None or not any(e.type == "system" for e in log.branch(target))
            else None,
        )
        self.store.prepare(pending)
        self._finish(log, pending)

    def step(self, log: SessionLog, kind: LiteralStep) -> str:
        with log.run_lock(), self.snapshots.workspace_lock(recovery=True):
            log.entries()
            if self._recover(log):
                return (
                    "Recovered the pending operation; no additional step was applied."
                )
            self._recover_turns(log)
            history = self.store.history(log.session_id)
            if not history.undo and not history.redo:
                return f"Nothing to {kind}."
            if history.head_id != log.head_id:
                raise SnapshotConflict(
                    "History navigation changed the undo context; use /rewind or start a new turn"
                )
            stack = history.undo if kind == "undo" else history.redo
            if not stack:
                return f"Nothing to {kind}."
            action = stack[-1]
            if action.scope != "conversation" and not action.complete:
                raise SnapshotConflict(
                    "Checkpoint has an unknown file outcome; use conversation-only rewind"
                )
            reverse = kind == "undo"
            changes = (
                [c.reverse() for c in reversed(action.changes)]
                if reverse
                else action.changes
            )
            target = action.before_head if reverse else action.after_head
            draft = (
                (action.user_id or action.before_draft)
                if reverse
                else action.after_draft
            )
            if action.scope == "code":
                target, draft = log.head_id, _draft(log)
            event = HistoryEvent(
                id=uuid4().hex, kind=kind, action_id=action.id, head_id=None
            )
            self._prepare(
                log,
                kind=kind,
                scope=action.scope,
                target=target,
                draft=draft,
                changes=changes,
                event=event,
            )
            return (
                f"{kind.capitalize()} applied: {action.kind}; {len(changes)} tracked file(s). "
                "Tools were not rerun. Untracked shell, hook, and external effects remain."
            )

    def plan(
        self,
        log: SessionLog,
        user_id: str,
        *,
        scope: Scope,
        end_id: str | None = None,
        paths: Sequence[str] | None = None,
    ) -> tuple[LogEntry, list[FileChange]]:
        if self.store.pending_path.exists():
            raise SnapshotConflict(
                "Recovery is pending; resume this session or run /undo to finish it first"
            )
        user = next(
            (
                e
                for e in log.branch()
                if e.id == user_id
                and e.type == "user"
                and e.data.get("source", "user") == "user"
            ),
            None,
        )
        if user is None:
            raise SnapshotConflict("Select a user prompt on the active path")
        if scope == "conversation":
            return user, []
        raw_changes = self.store.changes_since(log.session_id, user_id, end_id)
        if paths is not None:
            raw_changes = [c for c in raw_changes if c.path in set(paths)]
        changes = _combine(raw_changes)
        if paths is not None:
            requested = set(paths)
            if not requested or requested - {c.path for c in changes}:
                raise SnapshotConflict("Select files from the recovery preview")
            changes = [c for c in changes if c.path in requested]
        if self.read_only and changes:
            raise SnapshotConflict(
                "Code recovery is disabled in read-only mode; choose conversation-only rewind"
            )
        return user, [c.reverse() for c in reversed(changes)]

    def restore(
        self,
        log: SessionLog,
        user_id: str,
        *,
        scope: Scope,
        kind: LiteralRestore = "rewind",
        end_id: str | None = None,
        paths: Sequence[str] | None = None,
    ) -> str:
        with log.run_lock(), self.snapshots.workspace_lock(recovery=True):
            log.entries()
            if self._recover(log):
                return "Recovered the pending operation; no additional restore was applied."
            self._recover_turns(log)
            user, changes = self.plan(
                log, user_id, scope=scope, end_id=end_id, paths=paths
            )
            target = user.parent if scope != "code" else log.head_id
            draft = user.id if scope != "code" else _draft(log)
            action = Action(
                id=uuid4().hex,
                kind=kind,
                before_head=log.head_id,
                after_head=target,
                before_draft=_draft(log),
                after_draft=draft,
                scope=scope,
                changes=changes,
            )
            self._align(log)
            event = HistoryEvent(
                id=action.id, kind="action", action=action, head_id=None
            )
            self._prepare(
                log,
                kind=kind,
                scope=scope,
                target=target,
                draft=draft,
                changes=changes,
                event=event,
            )
            return f"Restored {scope}: {len(changes)} tracked file(s)."

    def request_context(self, log: SessionLog) -> tuple[str, ...]:
        path = self.store.root / "notice.json"
        if not path.exists():
            return ()
        notice = RestoreNotice.model_validate_json(path.read_bytes())
        marker = f"workspace-restore:{notice.id}"
        if any(
            marker in str(e.data.get("context", []))
            for e in log.branch()
            if e.type == "request"
        ):
            return ()
        files = ", ".join(notice.files[:20])
        return (
            f"<{marker}>Before this turn, a workspace restore affected {len(notice.files)} tracked file(s), including: {files}. Tool effects recorded before that restore may no longer hold; inspect current files.</{marker}>",
        )
