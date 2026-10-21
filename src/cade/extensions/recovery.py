"""恢复动作、轮次关联和待完成事务的持久化记录。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cade.extensions.snapshots import FileState, SnapshotConflict, _atomic_write

type Scope = Literal["conversation", "code", "both"]
type RecoveryKind = Literal["undo", "redo", "rewind", "revert"]


class Record(BaseModel):
    """磁盘记录拒绝未知字段和隐式类型转换。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class FileChange(Record):
    path: str
    before: FileState | None
    after: FileState | None

    def reverse(self) -> FileChange:
        return FileChange(path=self.path, before=self.after, after=self.before)


class Action(Record):
    id: str
    kind: Literal["turn", "rewind", "revert"]
    before_head: str | None
    after_head: str | None
    before_draft: str | None = None
    after_draft: str | None = None
    user_id: str | None = None
    scope: Scope = "both"
    changes: list[FileChange] = Field(default_factory=list)
    complete: bool = True
    warnings: list[str] = Field(default_factory=list)


class HistoryEvent(Record):
    id: str
    kind: Literal["action", "undo", "redo", "reset"]
    action_id: str | None = None
    action: Action | None = None
    head_id: str | None

    @model_validator(mode="after")
    def validate_action(self) -> HistoryEvent:
        if self.kind == "action":
            if (
                self.action is None
                or self.action.id != self.id
                or self.action_id is not None
            ):
                raise ValueError("Invalid action journal record")
        elif self.action is not None or (
            self.kind != "reset" and self.action_id is None
        ):
            raise ValueError("Invalid undo/redo journal record")
        return self


class History(Record):
    undo: list[Action] = Field(default_factory=list)
    redo: list[Action] = Field(default_factory=list)
    head_id: str | None = None


class Turn(Record):
    id: str
    session_id: str
    log_path: str
    workspace: str
    before_head: str | None
    before_draft: str | None
    user_id: str | None = None
    created: int


class Pending(Record):
    id: str
    session_id: str
    log_path: str
    expected_head: str | None
    target_head: str | None
    draft_id: str | None
    scope: Scope
    kind: RecoveryKind
    changes: list[FileChange]
    event: HistoryEvent
    system_prompt: str | None = None

    @model_validator(mode="after")
    def validate_plan(self) -> Pending:
        if self.event.id != self.id:
            raise ValueError("Recovery operation ID mismatch")
        if self.scope == "conversation" and self.changes:
            raise ValueError("Conversation-only restore cannot modify files")
        if self.scope == "code" and self.target_head != self.expected_head:
            raise ValueError("Code-only restore cannot change conversation ancestry")
        if self.kind in {"undo", "redo"}:
            if self.event.kind != self.kind:
                raise ValueError("Recovery step does not match its journal event")
        elif (
            self.event.kind != "action"
            or self.event.action is None
            or self.event.action.kind != self.kind
            or self.event.action.scope != self.scope
            or self.event.action.changes != self.changes
        ):
            raise ValueError("Recovery action does not match its plan")
        return self


class RestoreNotice(Record):
    id: str
    files: list[str]


class RecoveryStore:
    """文件内容留在快照仓库；这里保存不可变动作和恢复计划。"""

    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / ".cade" / "recovery"
        self.root.mkdir(parents=True, exist_ok=True)
        self.pending_path = self.root / "pending.json"

    def turn_directory(self, turn_id: str) -> Path:
        if len(turn_id) != 32 or any(c not in "0123456789abcdef" for c in turn_id):
            raise SnapshotConflict("Invalid checkpoint ID")
        directory = self.root / "turns" / turn_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_turn(self, turn: Turn) -> None:
        _atomic_write(
            self.turn_directory(turn.id) / "turn.json", turn.model_dump_json().encode()
        )

    def open_turns(self, session_id: str) -> list[Turn]:
        turns: list[Turn] = []
        for path in (self.root / "turns").glob("*/turn.json"):
            if (path.parent / "sealed").exists():
                continue
            turn = Turn.model_validate_json(path.read_bytes())
            if turn.session_id == session_id:
                turns.append(turn)
        return sorted(turns, key=lambda turn: turn.created)

    def seal(self, turn_id: str) -> None:
        _atomic_write(self.turn_directory(turn_id) / "sealed", b"sealed\n")

    def _events_path(self, session_id: str) -> Path:
        if not session_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in session_id
        ):
            raise SnapshotConflict("Invalid recovery session ID")
        directory = self.root / "sessions"
        directory.mkdir(exist_ok=True)
        return directory / f"{session_id}.jsonl"

    def events(self, session_id: str) -> list[HistoryEvent]:
        path = self._events_path(session_id)
        if not path.exists():
            return []
        data = path.read_bytes()
        lines = data.split(b"\n")
        if not data.endswith(b"\n"):
            lines = lines[:-1]
        events = [
            HistoryEvent.model_validate_json(line) for line in lines if line.strip()
        ]
        if len({event.id for event in events}) != len(events):
            raise SnapshotConflict("Duplicate recovery event ID")
        return events

    def append(self, session_id: str, event: HistoryEvent) -> None:
        if any(e.id == event.id for e in self.events(session_id)):
            return
        path = self._events_path(session_id)
        if path.exists():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                with path.open("r+b") as stream:
                    stream.truncate(data.rfind(b"\n") + 1)
                    stream.flush()
                    os.fsync(stream.fileno())
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(event.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def history(self, session_id: str) -> History:
        result = History()
        for event in self.events(session_id):
            if event.kind == "reset":
                result.undo.clear()
                result.redo.clear()
            elif event.kind == "action":
                if event.action is None:
                    raise SnapshotConflict("Action record is missing its payload")
                result.undo.append(event.action)
                result.redo.clear()
            elif event.kind == "undo":
                if not result.undo or result.undo[-1].id != event.action_id:
                    raise SnapshotConflict("Undo journal is inconsistent")
                result.redo.append(result.undo.pop())
            else:
                if not result.redo or result.redo[-1].id != event.action_id:
                    raise SnapshotConflict("Redo journal is inconsistent")
                result.undo.append(result.redo.pop())
            result.head_id = event.head_id
        return result

    def actions(self, session_id: str) -> list[Action]:
        return [e.action for e in self.events(session_id) if e.action is not None]

    def changes_since(
        self, session_id: str, user_id: str, end_id: str | None = None
    ) -> list[FileChange]:
        """按实际动作顺序合成代码变更，包含期间的撤销和重做。"""
        actions: dict[str, Action] = {}
        found = False
        ended = end_id is None
        changes: list[FileChange] = []
        for event in self.events(session_id):
            action = event.action
            if action is not None:
                actions[action.id] = action
                found = found or action.user_id == user_id
            elif event.kind in {"undo", "redo"}:
                action = actions.get(event.action_id or "")
                if action is None:
                    raise SnapshotConflict(
                        "Recovery journal references an unknown action"
                    )
            if action is not None and found:
                if action.scope != "conversation" and not action.complete:
                    raise SnapshotConflict(
                        "Selected range contains an incomplete code checkpoint"
                    )
                changes.extend(
                    [c.reverse() for c in reversed(action.changes)]
                    if event.kind == "undo"
                    else action.changes
                )
            if (
                end_id is not None
                and event.action is not None
                and found
                and event.action.user_id == end_id
            ):
                ended = True
                break
        if not found:
            raise SnapshotConflict(
                "This historical prompt has no code checkpoint in this session"
            )
        if not ended:
            raise SnapshotConflict("Invalid revert turn range")
        return changes

    def prepare(self, pending: Pending) -> None:
        if self.pending_path.exists():
            raise SnapshotConflict("A workspace recovery is already pending")
        _atomic_write(self.pending_path, pending.model_dump_json().encode())

    def pending(self) -> Pending | None:
        if not self.pending_path.exists():
            return None
        return Pending.model_validate_json(self.pending_path.read_bytes())

    def finish(self, pending: Pending) -> None:
        self.append(pending.session_id, pending.event)
        notice = RestoreNotice(
            id=pending.id, files=[change.path for change in pending.changes]
        )
        if pending.changes:
            _atomic_write(self.root / "notice.json", notice.model_dump_json().encode())
        self.pending_path.unlink()
