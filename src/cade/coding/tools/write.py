"""写入工作区文件并捕获快照。"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _resolve_authorized_path,
    display_path,
    text_arg,
)
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class WriteTool(WorkspaceTool):
    name = "write"
    description = "Write UTF-8 text to a file, creating parent directories."

    def __init__(
        self,
        workspace: Path,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        super().__init__(workspace)
        self.snapshots = snapshots

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"), writing=True)
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        if path.is_dir():
            raise ValueError(
                f"path is a directory: {display_path(self.workspace, path)}"
            )
        content = text_arg(args, "content")
        data = content.encode("utf-8")
        change = (
            self.snapshots.change(path)
            if self.snapshots and path.is_relative_to(self.workspace)
            else nullcontext()
        )
        with change:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return ToolResult(
            content=(f"Wrote {len(data)} bytes to {display_path(self.workspace, path)}")
        )
