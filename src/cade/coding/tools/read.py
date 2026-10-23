"""分页读取文本，按行数和字节数截断，并支持图片附件。"""

from __future__ import annotations

from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _resolve_authorized_path,
)
from cade.coding.tools.images import read_image
from cade.coding.tools.truncate import MAX_LINES, truncate
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class ReadTool(WorkspaceTool):
    name = "read"
    description = "Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). Images are sent as attachments. For text files, output is truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit for large files. When you need the full file, continue with offset until complete."

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of lines to read",
                },
            },
            "required": ["path"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"))
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        data = path.read_bytes()
        image = read_image(data)
        if image is not None:
            return image
        lines = data.decode("utf-8", "replace").splitlines(keepends=True)
        offset, limit = args.get("offset", 1), args.get("limit", MAX_LINES)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset <= 0:
            raise ValueError("offset must be a positive integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        start = offset - 1
        if start and start >= len(lines):
            raise ValueError(
                f"Offset {offset} is beyond end of file ({len(lines)} lines total)"
            )
        result = truncate("".join(lines[start : start + limit]))
        output = result.content
        if result.truncated:
            output += "\n\n[Output truncated at 2000 lines or 50KB. Use a shell command for long lines.]"
        elif start + limit < len(lines):
            output += (
                f"\n\n[More lines available. Use offset={offset + limit} to continue.]"
            )
        return ToolResult(output, metadata={"truncated": result.truncated})
