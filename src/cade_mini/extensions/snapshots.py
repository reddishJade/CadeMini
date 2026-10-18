"""工作区文件快照与 undo。"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Snapshot:
    """一次文件变更前的快照。"""

    id: str
    path: str
    existed: bool
    created_at: str


class SnapshotStore:
    """只记录 write/edit 工具触碰的文件；shell 变更不在范围内。"""

    def __init__(self, workspace: Path, *, root: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.root = (root or self.workspace / ".cade-mini" / "snapshots").resolve()
        self.index = self.root / "index.jsonl"

    def capture(self, path: Path) -> str | None:
        target = self._relative(path)
        existed = target.exists()
        data = b""
        if existed:
            data = target.read_bytes()
            if len(data) > _MAX_SNAPSHOT_BYTES:
                raise ValueError(
                    f"file too large to snapshot: {target} ({len(data)} bytes)"
                )
        snapshot_id = f"{time.time_ns()}-{secrets.token_hex(3)}"
        created_at = datetime.now(UTC).isoformat()
        directory = self.root / snapshot_id
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": snapshot_id,
            "path": self._relative_path(path),
            "existed": existed,
            "created_at": created_at,
        }
        (directory / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
        if existed:
            (directory / "payload.bin").write_bytes(data)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(metadata, ensure_ascii=False) + "\n")
        return snapshot_id

    def undo_last(self) -> Snapshot | None:
        entries = self._read_index()
        if not entries:
            return None
        metadata = entries.pop()
        snapshot_id = str(metadata.get("id", ""))
        relative = str(metadata.get("path", ""))
        target = (self.workspace / relative).resolve()
        if not _inside(target, self.workspace):
            raise ValueError(f"snapshot path escapes workspace: {relative}")
        if bool(metadata.get("existed")):
            payload = self.root / snapshot_id / "payload.bin"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.read_bytes())
        elif target.exists():
            target.unlink()
        self._write_index(entries)
        return Snapshot(
            id=snapshot_id,
            path=relative,
            existed=bool(metadata.get("existed")),
            created_at=str(metadata.get("created_at", "")),
        )

    def list(self, limit: int = 20) -> list[Snapshot]:
        entries = self._read_index()[-limit:]
        return [
            Snapshot(
                id=str(item.get("id", "")),
                path=str(item.get("path", "")),
                existed=bool(item.get("existed")),
                created_at=str(item.get("created_at", "")),
            )
            for item in entries
        ]

    def _relative(self, path: Path) -> Path:
        target = path.resolve()
        if not _inside(target, self.workspace):
            raise ValueError(f"path escapes workspace: {path}")
        return target

    def _relative_path(self, path: Path) -> str:
        return self._relative(path).relative_to(self.workspace).as_posix()

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        return entries

    def _write_index(self, entries: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries),
            encoding="utf-8",
        )


def _inside(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True
