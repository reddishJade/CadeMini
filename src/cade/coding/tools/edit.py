"""精确替换文件内容并捕获快照。"""

from __future__ import annotations

import errno
import os
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _resolve_authorized_path,
    text_arg,
)
from cade.coding.tools.edit_diff import Edit, apply_edits, diff_details, normalize_lf
from cade.coding.tools.mutation import file_mutation
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class EditTool(WorkspaceTool):
    name = "edit"
    description = "Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes."

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
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        "required": ["oldText", "newText"],
                    },
                },
            },
            "required": ["path", "edits"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"), writing=True)
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        raw = args.get("edits")
        if not isinstance(raw, list) or not raw:
            raise ValueError(
                "Edit tool input is invalid. edits must contain at least one replacement."
            )
        edits: list[Edit] = []
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("Each edit must contain oldText and newText")
            edits.append(Edit(text_arg(item, "oldText"), text_arg(item, "newText")))
        change = (
            self.snapshots.change(path)
            if self.snapshots and path.is_relative_to(self.workspace)
            else nullcontext()
        )
        with file_mutation(path), change:
            try:
                path.stat()
                if not os.access(path, os.R_OK | os.W_OK):
                    raise PermissionError(errno.EACCES, "Permission denied")
            except OSError as exc:
                code = errno.errorcode.get(exc.errno or errno.EIO, "EIO")
                raise ValueError(
                    f"Could not edit file: {text_arg(args, 'path')}. Error code: {code}."
                ) from exc
            text = path.read_bytes().decode("utf-8", errors="replace")
            bom = "\ufeff" if text.startswith("\ufeff") else ""
            content = text[len(bom) :]
            ending = (
                "\r\n"
                if "\r\n" in content and content.index("\r\n") < content.index("\n")
                else "\n"
            )
            before = normalize_lf(content)
            after = apply_edits(before, edits, text_arg(args, "path"))
            path.write_bytes(
                (
                    bom + (after.replace("\n", "\r\n") if ending == "\r\n" else after)
                ).encode("utf-8")
            )
        return ToolResult(
            content=f"Successfully replaced {len(edits)} block(s) in {text_arg(args, 'path')}.",
            metadata=diff_details(text_arg(args, "path"), before, after),
        )
