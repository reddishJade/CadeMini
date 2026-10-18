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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import filelock
from pydantic import BaseModel, ConfigDict, Field

_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024


class SnapshotConflict(ValueError):
    """文件已变化，或快照缺少安全恢复所需的信息。"""


class _FileState(BaseModel):
    """普通文件的内容地址和权限；不存在的文件用 null 表示。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=_MAX_SNAPSHOT_BYTES)
    mode: int = Field(ge=0, le=0o7777)


class _Manifest(BaseModel):
    """不可变的变更前清单。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    v: Literal[2] = 2
    id: str = Field(pattern=r"^[0-9]+-[0-9a-f]{6}$")
    path: str
    existed: bool
    created_at: str
    before: _FileState | None


class _Completion(BaseModel):
    """不可变的变更后状态。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    after: _FileState | None


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
        self.root = (root or self.workspace / ".cade-mini" / "snapshots").resolve()
        self.index = self.root / "index.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = filelock.FileLock(str(self.root / "snapshots.lock"))

    @contextmanager
    def change(self, path: Path) -> Iterator[None]:
        """串行记录一次变更；异常退出也保存实际结束状态。"""
        with self._lock:
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
            return snapshot_id

    def complete(self, snapshot_id: str) -> None:
        """发布结束状态一次；缺少结束状态的快照不能撤销。"""
        with self._lock:
            manifest = self._load(snapshot_id)
            destination = self.root / manifest.id / "after.json"
            if destination.exists():
                raise SnapshotConflict("Snapshot is already complete")
            state = self._state(self._target(self.workspace / manifest.path))
            _atomic_write(
                destination, _Completion(after=state).model_dump_json().encode()
            )

    def undo_last(self) -> Snapshot | None:
        with self._lock:
            entries = self._read_index()
            if not entries:
                return None
            metadata = entries[-1]
            if metadata.get("v") != 2:
                raise SnapshotConflict(
                    "Legacy snapshot has no post-change state; automatic undo is unsafe"
                )
            manifest = _Manifest.model_validate(metadata)
            saved = self._load(manifest.id)
            if saved != manifest:
                raise SnapshotConflict("Snapshot manifest does not match the index")
            completion = self.root / manifest.id / "after.json"
            if not completion.is_file():
                raise SnapshotConflict("Snapshot outcome is unknown; inspect workspace")
            after = _Completion.model_validate_json(completion.read_bytes()).after
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
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise SnapshotConflict(f"Symlink in snapshot target: {relative}")
        if target.is_relative_to(self.root):
            raise SnapshotConflict("Cannot snapshot snapshot storage")
        return target

    def _state(self, path: Path, *, save: bool = False) -> _FileState | None:
        try:
            metadata = path.stat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode):
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
        return _FileState(
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
