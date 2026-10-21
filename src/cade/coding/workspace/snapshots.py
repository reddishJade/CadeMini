"""工作区文件快照：校验变更后状态，再恢复不可变的原始内容。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import filelock
from pydantic import BaseModel, ConfigDict, Field

_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
_CAPTURE_FILE: ContextVar[tuple[Path, Path] | None] = ContextVar(
    "snapshot_capture_file", default=None
)


class SnapshotConflict(ValueError):
    """文件已变化，或快照缺少安全恢复所需的信息。"""


class FileState(BaseModel):
    """普通文件的内容地址和权限；不存在的文件用 null 表示。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=_MAX_SNAPSHOT_BYTES)
    mode: int = Field(ge=0, le=0o7777)


class _Manifest(BaseModel):
    """不可变的变更前清单。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[0-9]+-[0-9a-f]{6}$")
    path: str
    existed: bool
    created_at: str
    before: FileState | None


class _Completion(BaseModel):
    """不可变的变更后状态。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    after: FileState | None


@dataclass(frozen=True)
class Snapshot:
    """一次文件变更前的快照。"""

    id: str
    path: str
    existed: bool
    created_at: str


class SnapshotStore:
    """捕获 write/edit 和已识别的 shell 目标；不扫描整个工作区。"""

    def __init__(self, workspace: Path, *, root: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.root = (root or self.workspace / ".cade" / "snapshots").resolve()
        self.index = self.root / "index.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = filelock.FileLock(str(self.root / "snapshots.lock"))
        recovery_root = self.workspace / ".cade" / "recovery"
        recovery_root.mkdir(parents=True, exist_ok=True)
        self._workspace_lock = filelock.FileLock(
            str(recovery_root / "workspace.lock"), timeout=0
        )

    @contextmanager
    def change(self, path: Path) -> Iterator[None]:
        """串行记录一次变更；异常退出也保存实际结束状态。"""
        with self.workspace_lock(), self._lock:
            snapshot_id = self.capture(path)
            try:
                yield
            finally:
                self.complete(snapshot_id)

    def capture(self, path: Path) -> str:
        """保存变更前状态；调用方必须在变更后调用 complete。"""
        with self._lock:
            target = self._target(path)
            before = self._state(target, save=True)
            snapshot_id = f"{time.time_ns()}-{secrets.token_hex(3)}"
            manifest = _Manifest(
                id=snapshot_id,
                path=target.relative_to(self.workspace).as_posix(),
                existed=before is not None,
                created_at=datetime.now(UTC).isoformat(),
                before=before,
            )
            directory = self.root / snapshot_id
            directory.mkdir()
            _atomic_write(directory / "meta.json", manifest.model_dump_json().encode())
            entries = self._read_index()
            entries.append(manifest.model_dump(mode="json"))
            self._write_index(entries)
            scope = _CAPTURE_FILE.get()
            capture_file = (
                scope[1] if scope is not None and scope[0] == self.workspace else None
            )
            if capture_file is not None:
                with capture_file.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(snapshot_id + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            return snapshot_id

    def complete(self, snapshot_id: str) -> None:
        """发布结束状态一次；缺少结束状态的快照不能撤销。"""
        with self._lock:
            manifest = self._load(snapshot_id)
            destination = self.root / manifest.id / "after.json"
            if destination.exists():
                raise SnapshotConflict("Snapshot is already complete")
            state = self._state(self._target(self.workspace / manifest.path), save=True)
            _atomic_write(
                destination, _Completion(after=state).model_dump_json().encode()
            )

    def undo_last(self) -> Snapshot | None:
        with self.workspace_lock(), self._lock:
            entries = self._read_index()
            if not entries:
                return None
            metadata = entries[-1]
            manifest = _Manifest.model_validate(metadata)
            saved = self._load(manifest.id)
            if saved != manifest:
                raise SnapshotConflict("Snapshot manifest does not match the index")
            completion = self.root / manifest.id / "after.json"
            if not completion.is_file():
                raise SnapshotConflict("Snapshot outcome is unknown; inspect workspace")
            after = _Completion.model_validate_json(completion.read_bytes()).after
            if after is not None and (self.root / "blobs" / after.sha256).exists():
                self.verify_payload(after)
            target = self._target(self.workspace / manifest.path)
            if self._state(target) != after:
                raise SnapshotConflict(f"File changed since snapshot: {manifest.path}")
            if manifest.before is None:
                target.unlink(missing_ok=True)
            else:
                state = manifest.before
                payload = (self.root / "blobs" / state.sha256).read_bytes()
                if len(payload) != state.size or _digest(payload) != state.sha256:
                    raise SnapshotConflict("Snapshot payload is corrupted")
                _atomic_write(target, payload, mode=state.mode)
            self._write_index(entries[:-1])
            return _snapshot(manifest.model_dump())

    def list(self, limit: int = 20) -> list[Snapshot]:
        with self._lock:
            return [_snapshot(item) for item in self._read_index()[-limit:]]

    @contextmanager
    def workspace_lock(self, *, recovery: bool = False) -> Iterator[None]:
        """串行化工作区写入；轮次内的工具继承已取得的锁。"""
        scope = _CAPTURE_FILE.get()
        if scope is not None and scope[0] == self.workspace:
            yield
            return
        directory = self.workspace / ".cade" / "recovery"
        directory.mkdir(parents=True, exist_ok=True)
        lock = self._workspace_lock
        try:
            lock.acquire()
        except filelock.Timeout as exc:
            raise SnapshotConflict("Workspace is busy in another session") from exc
        try:
            if not recovery and (directory / "pending.json").exists():
                raise SnapshotConflict(
                    "Workspace recovery is pending; resume its session first"
                )
            yield
        finally:
            lock.release()

    @contextmanager
    def recording(self, capture_file: Path) -> Iterator[None]:
        """关联当前轮次；线程和同步子代理继承该上下文。"""
        token = _CAPTURE_FILE.set((self.workspace, capture_file))
        try:
            yield
        finally:
            _CAPTURE_FILE.reset(token)

    def normalize_path(self, path: str) -> str:
        """校验工作区边界和链接，返回规范的相对路径。"""
        return (
            self._target(self.workspace / path).relative_to(self.workspace).as_posix()
        )

    def capture_state(self, path: str) -> FileState | None:
        """保存普通文件当前内容，返回可用于双向恢复的状态。"""
        with self._lock:
            return self._state(self._target(self.workspace / path), save=True)

    def verify_state(self, path: str, expected: FileState | None) -> None:
        with self._lock:
            if self._state(self._target(self.workspace / path)) != expected:
                raise SnapshotConflict(f"File changed since checkpoint: {path}")

    def verify_payload(self, state: FileState | None) -> bytes:
        if state is None:
            return b""
        payload = (self.root / "blobs" / state.sha256).read_bytes()
        if len(payload) != state.size or _digest(payload) != state.sha256:
            raise SnapshotConflict("Snapshot payload is corrupted")
        return payload

    def apply_state(
        self, path: str, expected: FileState | None, desired: FileState | None
    ) -> None:
        """调用方持有工作区锁；再次校验目标后原子恢复单个文件。"""
        with self._lock:
            self.verify_state(path, expected)
            target = self._target(self.workspace / path)
            if desired is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write(target, self.verify_payload(desired), mode=desired.mode)

    def captured_change(
        self, snapshot_id: str
    ) -> tuple[str, FileState | None, FileState | None]:
        with self._lock:
            manifest = self._load(snapshot_id)
            completion = self.root / manifest.id / "after.json"
            if not completion.is_file():
                raise SnapshotConflict("Snapshot outcome is unknown; inspect workspace")
            after = _Completion.model_validate_json(completion.read_bytes()).after
            self.verify_payload(manifest.before)
            self.verify_payload(after)
            return manifest.path, manifest.before, after

    def _load(self, snapshot_id: str) -> _Manifest:
        # 先验证 ID，再用它拼接存储路径。
        if not snapshot_id or any(c not in "0123456789abcdef-" for c in snapshot_id):
            raise SnapshotConflict("Invalid snapshot id")
        manifest = _Manifest.model_validate_json(
            (self.root / snapshot_id / "meta.json").read_bytes()
        )
        if manifest.id != snapshot_id:
            raise SnapshotConflict("Snapshot id mismatch")
        return manifest

    def _target(self, path: Path) -> Path:
        target = Path(os.path.abspath(path))
        if not target.is_relative_to(self.workspace) or target == self.workspace:
            raise SnapshotConflict(f"Snapshot path escapes workspace: {path}")
        relative = target.relative_to(self.workspace)
        current = self.workspace
        if any(part in {".git", ".cade"} for part in relative.parts):
            raise SnapshotConflict(f"Protected recovery target: {relative}")
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise SnapshotConflict(f"Symlink in snapshot target: {relative}")
        if target.is_relative_to(self.root):
            raise SnapshotConflict("Cannot snapshot snapshot storage")
        return target

    def _state(self, path: Path, *, save: bool = False) -> FileState | None:
        try:
            metadata = path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
            raise SnapshotConflict(f"Snapshot target is not a regular file: {path}")
        if metadata.st_size > _MAX_SNAPSHOT_BYTES:
            raise SnapshotConflict(f"File too large to snapshot: {path}")
        data = path.read_bytes()
        if len(data) > _MAX_SNAPSHOT_BYTES:
            raise SnapshotConflict(f"File too large to snapshot: {path}")
        digest = _digest(data)
        if save:
            blob = self.root / "blobs" / digest
            if blob.exists():
                if _digest(blob.read_bytes()) != digest:
                    raise SnapshotConflict("Snapshot payload is corrupted")
            else:
                _atomic_write(blob, data)
        return FileState(
            sha256=digest, size=len(data), mode=stat.S_IMODE(metadata.st_mode)
        )

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.index.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise SnapshotConflict("Invalid snapshot index")
            entries.append(item)
        return entries

    def _write_index(self, entries: list[dict[str, Any]]) -> None:
        data = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries)
        _atomic_write(self.index, data.encode())


def _snapshot(item: dict[str, Any]) -> Snapshot:
    return Snapshot(
        id=str(item["id"]),
        path=str(item["path"]),
        existed=bool(item["existed"]),
        created_at=str(item["created_at"]),
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """先同步临时文件，再原子替换目标，保留指定权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.chmod(name, mode)
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
