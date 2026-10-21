"""读取工作区文本文件和目录。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    MAX_FILE_BYTES,
    MAX_LINE_CHARS,
    MAX_READ_BYTES,
    MAX_READ_LINES,
    WorkspaceTool,
    _ensure_not_blocked,
    _is_blocked,
    _resolve_authorized_path,
    display_path,
    int_arg,
    looks_binary,
)
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class ReadTool(WorkspaceTool):
    name = "read"
    description = (
        "Read a text file or list a directory. Use offset and limit to page "
        "through large files. Paths are relative to the workspace."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"))
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        offset = int_arg(args, "offset", 1, 1, 1_000_000)
        limit = int_arg(args, "limit", MAX_READ_LINES, 1, MAX_READ_LINES)
        if path.is_dir():
            return _read_directory(self.workspace, path, offset, limit)
        if not path.is_file():
            raise ValueError(f"file not found: {display_path(self.workspace, path)}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"file is too large: {size} bytes")
        data = path.read_bytes()
        if looks_binary(data):
            raise ValueError(
                f"cannot read binary file: {display_path(self.workspace, path)}"
            )
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return ToolResult(
                content=f"{display_path(self.workspace, path)} (empty file)"
            )
        if offset > total:
            raise ValueError(f"offset {offset} is beyond the {total} lines")
        selected = lines[offset - 1 : offset - 1 + limit]
        output: list[str] = []
        used = 0
        last = offset - 1
        for index, line in enumerate(selected, start=offset):
            rendered = line[:MAX_LINE_CHARS]
            if len(line) > MAX_LINE_CHARS:
                rendered += "..."
            row = f"{index}: {rendered}"
            used += len(row.encode("utf-8")) + 1
            if used > MAX_READ_BYTES:
                output.append(
                    f"(output capped at {MAX_READ_BYTES // 1024} KB; "
                    f"continue with offset={last + 1})"
                )
                break
            output.append(row)
            last = index
        return ToolResult(
            content=(
                f"{display_path(self.workspace, path)} "
                f"(lines {offset}-{last} of {total}):\n" + "\n".join(output)
            ),
            metadata={"path": display_path(self.workspace, path)},
        )


def _read_directory(
    root: Path,
    path: Path,
    offset: int,
    limit: int,
) -> ToolResult:
    entries = sorted(
        (entry.name + ("/" if entry.is_dir() else ""), entry.is_dir())
        for entry in path.iterdir()
        if not _is_blocked(root, entry)
    )
    selected = entries[offset - 1 : offset - 1 + limit]
    names = [name for name, _ in selected]
    suffix = (
        f"\n({len(entries)} entries; next offset={offset + len(selected)})"
        if offset - 1 + len(selected) < len(entries)
        else f"\n({len(entries)} entries)"
    )
    return ToolResult(
        content=(f"{display_path(root, path) or '.'}/:\n" + "\n".join(names) + suffix)
    )
