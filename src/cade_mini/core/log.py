"""Cade Mini 的 append-only JSONL 会话日志。"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import filelock

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
    """单分支、单 writer 的会话事实日志。

    读取忽略未换行的末尾残片；追加前截断残片，并在文件锁内重新读取 head。
    文件锁保证单次追加连续，但调用方仍需避免并发运行同一会话。
    """

    def __init__(self, path: Path, *, lock_timeout: float = 10.0) -> None:
        self.path = path.resolve()
        if not self.path.parent.is_dir():
            raise LogError(f"session directory does not exist: {self.path.parent}")
        self._lock = filelock.FileLock(
            str(self.path.with_suffix(".lock")),
            timeout=lock_timeout,
        )
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
    ) -> SessionLog:
        """创建会话文件并写入 meta 事件。"""
        sessions_dir = sessions_dir.resolve()
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = session_id or uuid4().hex[:12]
        if not _SESSION_ID.fullmatch(sid):
            raise LogError(f"invalid session id: {sid!r}")
        path = sessions_dir / f"session-{sid}.jsonl"
        if path.exists():
            raise LogError(f"session already exists: {path}")
        log = cls(path)
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

    def append(self, event_type: EventType, data: dict[str, JsonValue]) -> LogEntry:
        """追加一条事实并 fsync；返回已提交的条目。"""
        with self._lock:
            self._repair_tail()
            entries = self._read_entries()
            self._head = entries[-1] if entries else None
            entry = LogEntry(
                id=uuid4().hex[:12],
                parent=self._head.id if self._head is not None else None,
                ts=_now(),
                type=event_type,
                data=data,
            )
            line = entry.model_dump_json()
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._head = entry
            return entry

    def copy_branch(
        self,
        sessions_dir: Path,
        *,
        before_entry_id: str | None = None,
        drop_recent_users: int = 0,
        project_root: Path | None = None,
    ) -> SessionLog:
        """把当前分支复制成新会话，可截断到某个 entry 或最近用户轮次之前。"""
        branch = self.branch()
        if before_entry_id is not None:
            index = next(
                (
                    position
                    for position, entry in enumerate(branch)
                    if entry.id == before_entry_id
                ),
                None,
            )
            if index is None:
                raise LogError(f"entry not found on branch: {before_entry_id}")
            branch = branch[:index]
        elif drop_recent_users > 0:
            user_indices = [
                position
                for position, entry in enumerate(branch)
                if entry.type == "user"
            ]
            if drop_recent_users >= len(user_indices):
                branch = []
            else:
                branch = branch[: user_indices[-drop_recent_users]]
        target = SessionLog.create(
            sessions_dir,
            project_root=project_root,
        )
        id_map: dict[str, str] = {}
        for entry in branch:
            if entry.type == "meta":
                id_map[entry.id] = target.entries()[0].id
                continue
            data = dict(entry.data)
            for key in ("first_kept_id", "start_id"):
                value = data.get(key)
                if isinstance(value, str) and value in id_map:
                    data[key] = id_map[value]
            copied = target.append(entry.type, data)
            id_map[entry.id] = copied.id
        return target

    def entries(self) -> list[LogEntry]:
        """读取全部已提交条目；忽略未换行的末尾残片。"""
        with self._lock:
            entries = self._read_entries()
            self._head = entries[-1] if entries else None
            return entries

    def branch(self) -> list[LogEntry]:
        """从当前 head 回溯到根，返回按时间正序的分支。"""
        entries = self.entries()
        if not entries:
            return []
        by_id = {entry.id: entry for entry in entries}
        current: str | None = entries[-1].id
        branch: list[LogEntry] = []
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise LogError(f"session branch contains a cycle at {current}")
            entry = by_id.get(current)
            if entry is None:
                raise LogError(f"session branch is missing parent {current}")
            branch.append(entry)
            seen.add(current)
            current = entry.parent
        branch.reverse()
        return branch

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
                entries.append(LogEntry.model_validate_json(raw))
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
    seen: set[str] = set()
    for entry in entries:
        if entry.id in seen:
            raise LogError(f"duplicate session entry id in {path}: {entry.id}")
        if entry.parent is not None and entry.parent not in seen:
            raise LogError(
                f"missing or forward session parent in {path}: {entry.parent}"
            )
        seen.add(entry.id)


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
            entries = log.branch()
        except (LogError, OSError, ValueError):
            continue
        if not entries:
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
                updated_at=entries[-1].ts,
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
