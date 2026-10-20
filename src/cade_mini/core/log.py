"""Cade Mini 的 append-only JSONL 会话日志。"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import uuid4

import filelock

from cade_mini.core.events import validate_event_data
from cade_mini.core.types import EventType, JsonValue, LogEntry, LogError

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SESSION_FILE = re.compile(r"^session-([A-Za-z0-9][A-Za-z0-9_-]*)\.jsonl$")


@dataclass(frozen=True)
class SessionInfo:
    """会话列表的展示投影；标题来自第一条用户输入。"""

    id: str
    path: Path
    title: str
    created_at: str
    updated_at: str


class SessionLog:
    """单 writer 的会话树日志；最后一条记录确定活动分支。

    读取忽略未换行的末尾残片；追加前截断残片，并在文件锁内重新读取 head。
    文件锁保证单次追加连续，但调用方仍需避免并发运行同一会话。
    """

    def __init__(
        self, path: Path, *, lock_timeout: float = 10.0, deferred: bool = False
    ) -> None:
        self.path = path.resolve()
        if not self.path.parent.is_dir():
            raise LogError(f"session directory does not exist: {self.path.parent}")
        self._lock = filelock.FileLock(
            str(self.path.with_suffix(".lock")),
            timeout=lock_timeout,
        )
        self._memory_lock = RLock()
        self._pending: list[LogEntry] | None = [] if deferred else None
        self._head: LogEntry | None = None
        if not deferred:
            with self._lock:
                entries = self._read_entries()
                self._head = entries[-1] if entries else None

    @classmethod
    def create(
        cls,
        sessions_dir: Path,
        *,
        session_id: str | None = None,
        project_root: Path | None = None,
        deferred: bool = False,
    ) -> SessionLog:
        """创建会话；deferred 模式在首条对话消息到来前只保留内存记录。"""
        sessions_dir = sessions_dir.resolve()
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = session_id or uuid4().hex[:12]
        if not _SESSION_ID.fullmatch(sid):
            raise LogError(f"invalid session id: {sid!r}")
        path = sessions_dir / f"session-{sid}.jsonl"
        if path.exists():
            raise LogError(f"session already exists: {path}")
        log = cls(path, deferred=deferred)
        log.append(
            "meta",
            {
                "session": sid,
                "project": str(project_root or Path.cwd()),
                "created": _now(),
            },
        )
        return log

    @classmethod
    def open(cls, path: Path) -> SessionLog:
        """打开已有会话；只接受 session-<id>.jsonl。"""
        resolved = path.resolve()
        if _SESSION_FILE.fullmatch(resolved.name) is None:
            raise LogError(f"not a session file: {resolved}")
        if not resolved.is_file():
            raise LogError(f"session file does not exist: {resolved}")
        return cls(resolved)

    @property
    def session_id(self) -> str:
        match = _SESSION_FILE.fullmatch(self.path.name)
        if match is None:
            raise LogError(f"not a session file: {self.path}")
        return match.group(1)

    @contextmanager
    def run_lock(self) -> Iterator[None]:
        """独占整个会话操作；发生竞争时立即拒绝，退出时释放。"""
        lock = filelock.FileLock(str(self.path.with_suffix(".run.lock")), timeout=0)
        try:
            lock.acquire()
        except filelock.Timeout as exc:
            raise LogError(f"session is already running: {self.session_id}") from exc
        try:
            yield
        finally:
            lock.release()

    def append(self, event_type: EventType, data: dict[str, JsonValue]) -> LogEntry:
        """校验并追加 v4 事实，fsync 后返回已提交的条目。"""
        return self._append(event_type, data, version=4)

    def _append(
        self,
        event_type: EventType,
        data: dict[str, JsonValue],
        *,
        version: Literal[1, 2, 3, 4],
    ) -> LogEntry:
        if version >= 2:
            try:
                validate_event_data(event_type, data)
            except ValueError as exc:
                raise LogError(f"invalid {event_type} event payload") from exc
        with (
            self._memory_lock,
            self._lock if self._pending is None else nullcontext(),
        ):
            entries = self._read_entries() if self._pending is None else self._pending
            parent = entries[-1].id if entries else None
            if event_type == "branch":
                selected = next(
                    (e for e in entries if e.id == data["selected_id"]), None
                )
                if selected is None:
                    raise LogError(f"session entry not found: {data['selected_id']}")
                parent = selected.parent if selected.type == "user" else selected.id
            if event_type == "restore":
                parent = _restore_parent(data, {e.id: e for e in entries})
            entry = LogEntry(
                v=version,
                id=uuid4().hex[:12],
                parent=parent,
                ts=_now(),
                type=event_type,
                data=data,
            )
            if event_type == "checkpoint":
                _validate_entries([*entries, entry], self.path)
            if self._pending is None:
                self._repair_tail()
                with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(entry.model_dump_json() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            else:
                self._pending.append(entry)
                if _is_conversation(entry):
                    self._flush_pending()
            self._head = entry
            return entry

    def _flush_pending(self) -> None:
        """首次落盘时独占创建文件，避免覆盖另一实例创建的同名会话。"""
        if self._pending is None:
            return
        with self._lock:
            try:
                with self.path.open("x", encoding="utf-8", newline="\n") as stream:
                    for entry in self._pending:
                        stream.write(entry.model_dump_json() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise LogError(f"session already exists: {self.path}") from exc
            self._pending = None

    def switch_branch(self, selected_id: str) -> LogEntry:
        """在原文件追加导航事实；用户消息定位到其父节点，其他记录定位到自身。"""
        with self.run_lock():
            return self.append("branch", {"selected_id": selected_id})

    @property
    def navigation_draft(self) -> str | None:
        """恢复尚未重发的历史输入；新对话消息会结束该草稿状态。"""
        entries = self.entries()
        branch = _branch(entries)
        for entry in reversed(branch):
            if entry.type in {"user", "assistant"}:
                return None
            if entry.type == "restore":
                draft_id = entry.data["draft_id"]
                return next(
                    (str(e.data.get("text", "")) for e in entries if e.id == draft_id),
                    "",
                )
            if entry.type == "branch":
                selected = next(e for e in entries if e.id == entry.data["selected_id"])
                return (
                    str(selected.data.get("text", ""))
                    if selected.type == "user"
                    else ""
                )
        return None

    def copy_branch(
        self,
        sessions_dir: Path,
        *,
        before_entry_id: str | None = None,
        drop_recent_users: int = 0,
        project_root: Path | None = None,
        deferred: bool = False,
    ) -> SessionLog:
        """复制活动路径或任意历史节点之前的路径，不复制兄弟分支及导航控制记录。"""
        with self.run_lock():
            branch = self.branch(before_entry_id)
            if before_entry_id is not None:
                branch = branch[:-1]
            elif drop_recent_users > 0:
                user_indices = [i for i, e in enumerate(branch) if e.type == "user"]
                branch = (
                    []
                    if drop_recent_users >= len(user_indices)
                    else branch[: user_indices[-drop_recent_users]]
                )
            target = SessionLog.create(
                sessions_dir, project_root=project_root, deferred=True
            )
            copied_entries = target.entries()
            id_map: dict[str, str] = {}
            for entry in branch:
                if entry.type == "meta":
                    id_map[entry.id] = copied_entries[0].id
                    continue
                if entry.type in {"branch", "checkpoint", "restore"}:
                    continue
                data = dict(entry.data)
                for key in ("first_kept_id", "start_id"):
                    value = data.get(key)
                    if isinstance(value, str) and value in id_map:
                        data[key] = id_map[value]
                copied = LogEntry(
                    v=entry.v,
                    id=uuid4().hex[:12],
                    parent=copied_entries[-1].id,
                    ts=entry.ts,
                    type=entry.type,
                    data=data,
                )
                copied_entries.append(copied)
                id_map[entry.id] = copied.id
            target._pending = copied_entries
            target._head = copied_entries[-1]
            if not deferred or any(_is_conversation(e) for e in copied_entries):
                target._flush_pending()
            return target

    def entries(self) -> list[LogEntry]:
        """读取全部已提交条目；忽略未换行的末尾残片。"""
        with self._memory_lock:
            if self._pending is not None:
                return list(self._pending)
            with self._lock:
                entries = self._read_entries()
                self._head = entries[-1] if entries else None
                return entries

    def branch(self, entry_id: str | None = None) -> list[LogEntry]:
        """返回活动路径或指定历史节点到根的路径，允许访问非活动分支。"""
        return _branch(self.entries(), entry_id)

    @property
    def head_id(self) -> str | None:
        return self._head.id if self._head is not None else None

    def _read_entries(self) -> list[LogEntry]:
        if not self.path.exists():
            return []
        data = self.path.read_bytes()
        if not data:
            return []
        lines = data.split(b"\n")
        if not data.endswith(b"\n"):
            # 未换行的末尾是崩溃残片，不属于已提交事实。
            lines = lines[:-1]
        entries: list[LogEntry] = []
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                entry = LogEntry.model_validate_json(raw)
                if entry.v >= 2:
                    validate_event_data(entry.type, entry.data)
                entries.append(entry)
            except ValueError as exc:
                raise LogError(f"invalid session record: {self.path}:{number}") from exc
        _validate_entries(entries, self.path)
        return entries

    def _repair_tail(self) -> None:
        """追加前截断崩溃残片，避免新记录拼到半行后面。"""
        if not self.path.exists():
            return
        with self.path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            if end == 0:
                return
            stream.seek(end - 1)
            if stream.read(1) == b"\n":
                return
        data = self.path.read_bytes()
        last_newline = data.rfind(b"\n")
        with self.path.open("r+b") as stream:
            stream.truncate(last_newline + 1 if last_newline >= 0 else 0)
            stream.flush()
            os.fsync(stream.fileno())


def _validate_entries(entries: list[LogEntry], path: Path) -> None:
    """校验 ID 唯一性和 parent 只能指向更早的条目。"""
    seen: dict[str, LogEntry] = {}
    for entry in entries:
        if entry.id in seen:
            raise LogError(f"duplicate session entry id in {path}: {entry.id}")
        if entry.parent is not None and entry.parent not in seen:
            raise LogError(
                f"missing or forward session parent in {path}: {entry.parent}"
            )
        if entry.type == "branch":
            selected = seen.get(str(entry.data["selected_id"]))
            if selected is None:
                raise LogError(
                    f"missing or forward navigation target in {path}: {entry.id}"
                )
            parent = selected.parent if selected.type == "user" else selected.id
            if entry.parent != parent:
                raise LogError(
                    f"navigation parent does not match selected entry: {entry.id}"
                )
        if entry.type == "restore" and entry.parent != _restore_parent(
            entry.data, seen
        ):
            raise LogError(f"invalid restore parent: {entry.id}")
        if entry.type == "checkpoint":
            user = seen.get(str(entry.data["user_id"]))
            if user is None or user.type != "user":
                raise LogError(f"invalid checkpoint user: {entry.id}")
            anchor = entry.data["anchor_id"]
            current = user.parent
            while current is not None and current != anchor:
                current = seen[current].parent
            if current != anchor:
                raise LogError(f"invalid checkpoint anchor: {entry.id}")
        seen[entry.id] = entry


def _restore_parent(
    data: dict[str, JsonValue], seen: dict[str, LogEntry]
) -> str | None:
    target = data["target_id"]
    if data["scope"] == "code" and target != next(reversed(seen), None):
        raise LogError("Code-only restore cannot change conversation ancestry")
    if target is not None and (not isinstance(target, str) or target not in seen):
        raise LogError("Restore target must reference an earlier entry")
    draft = data["draft_id"]
    if draft is not None and (
        not isinstance(draft, str) or draft not in seen or seen[draft].type != "user"
    ):
        raise LogError("Restore draft must reference an earlier user input")
    return target if isinstance(target, str) else None


def _branch(entries: Sequence[LogEntry], entry_id: str | None = None) -> list[LogEntry]:
    """从同一个读取快照构造路径，避免列表与 head 来自不同文件状态。"""
    if not entries:
        if entry_id is not None:
            raise LogError(f"session entry not found: {entry_id}")
        return []
    by_id = {entry.id: entry for entry in entries}
    current: str | None = entry_id if entry_id is not None else entries[-1].id
    branch: list[LogEntry] = []
    seen: set[str] = set()
    while current is not None:
        if current in seen:
            raise LogError(f"session branch contains a cycle at {current}")
        entry = by_id.get(current)
        if entry is None:
            raise LogError(f"session entry not found: {current}")
        branch.append(entry)
        seen.add(current)
        current = entry.parent
    branch.reverse()
    return branch


def _is_conversation(entry: LogEntry) -> bool:
    return (
        entry.type == "user"
        and entry.data.get("source", "user") == "user"
        and bool(str(entry.data.get("text", "")).strip())
    ) or (entry.type == "assistant" and bool(entry.data.get("blocks")))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def session_title(entries: Sequence[LogEntry]) -> str:
    """取第一条非空用户输入作为会话标题。"""
    for entry in entries:
        if entry.type != "user":
            continue
        text = entry.data.get("text")
        if not isinstance(text, str):
            continue
        collapsed = " ".join(text.split())
        if collapsed:
            return collapsed[:70] + ("..." if len(collapsed) > 70 else "")
    return "(untitled)"


def list_sessions(sessions_dir: Path) -> list[SessionInfo]:
    """按最近活动排序列出可读会话；损坏文件跳过。"""
    root = sessions_dir.resolve()
    if not root.is_dir():
        return []
    infos: list[SessionInfo] = []
    for path in root.glob("session-*.jsonl"):
        try:
            log = SessionLog.open(path)
            entries = log.entries()
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
                timespec="seconds"
            )
        except (LogError, OSError, ValueError):
            continue
        if not any(
            entry.type == "user"
            and entry.data.get("source", "user") == "user"
            and str(entry.data.get("text", "")).strip()
            for entry in entries
        ):
            continue
        meta = next((entry for entry in entries if entry.type == "meta"), None)
        created_at = (
            str(meta.data.get("created", entries[0].ts))
            if meta is not None
            else entries[0].ts
        )
        infos.append(
            SessionInfo(
                id=log.session_id,
                path=path,
                title=session_title(entries),
                created_at=created_at,
                updated_at=modified_at,
            )
        )

    def _sort_key(info: SessionInfo) -> tuple[int, str]:
        try:
            mtime = info.path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        return mtime, info.updated_at

    infos.sort(key=_sort_key, reverse=True)
    return infos
