"""精确替换文件内容并捕获快照。"""

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


class EditTool(WorkspaceTool):
    name = "edit"
    description = (
        "Replace one exact occurrence of old text with new text. The old text "
        "must appear exactly once."
    )

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
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"), writing=True)
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        if not path.is_file():
            raise ValueError(f"file not found: {display_path(self.workspace, path)}")
        old = text_arg(args, "old")
        new = text_arg(args, "new")
        if not old:
            raise ValueError("old must not be empty")
        text = path.read_bytes().decode("utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            raise ValueError("old text was not found")
        if count > 1:
            raise ValueError(f"old text appears {count} times; make it unique")
        change = (
            self.snapshots.change(path)
            if self.snapshots and path.is_relative_to(self.workspace)
            else nullcontext()
        )
        with change:
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        return ToolResult(
            content=(
                f"Edited {display_path(self.workspace, path)} "
                f"({len(old)} -> {len(new)} characters)"
            )
        )
