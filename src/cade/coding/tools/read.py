"""分页读取文本，按行数和字节数截断，并支持图片附件。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _resolve_authorized_path,
    text_arg,
)
from cade.coding.tools.images import read_image
from cade.coding.tools.truncate import MAX_BYTES, format_size, truncate
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
                    "type": "number",
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of lines to read",
                },
            },
            "required": ["path"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"))
        if not path.exists():
            raw = str(path)
            variants = [
                re.sub(r" (AM|PM)\.", "\u202f\\1.", raw, flags=re.IGNORECASE),
                unicodedata.normalize("NFD", raw),
                raw.replace("'", "\u2019"),
                unicodedata.normalize("NFD", raw).replace("'", "\u2019"),
            ]
            for variant in variants:
                candidate = Path(variant)
                if candidate.exists():
                    path = candidate
                    break
        _ensure_not_blocked(self.workspace, path, allow_sensitive=True)
        data = path.read_bytes()
        image = read_image(data)
        if image is not None:
            return image
        lines = data.decode("utf-8", "replace").split("\n")
        offset = args.get("offset")
        start = (
            max(0, int(offset) - 1) if isinstance(offset, int | float) and offset else 0
        )
        if start >= len(lines):
            raise ValueError(
                f"Offset {offset} is beyond end of file ({len(lines)} lines total)"
            )
        limit = args.get("limit")
        end = (
            min(start + int(limit), len(lines))
            if isinstance(limit, int | float)
            else len(lines)
        )
        # JavaScript 的 slice 对负数终点从数组尾部计数。
        selected = lines[start : end if end >= 0 else max(len(lines) + end, 0)]
        result = truncate("\n".join(selected))
        output = result.content
        details: dict[str, Any] | None = None
        if result.firstLineExceedsLimit:
            size = format_size(len(lines[start].encode("utf-8")))
            output = f"[Line {start + 1} is {size}, exceeds {format_size(MAX_BYTES)} limit. Use bash: sed -n '{start + 1}p' {text_arg(args, 'path')} | head -c {MAX_BYTES}]"
            details = {"truncation": result.record()}
        elif result.truncated:
            last = start + result.outputLines
            byte_note = (
                f" ({format_size(MAX_BYTES)} limit)"
                if result.truncatedBy == "bytes"
                else ""
            )
            output += f"\n\n[Showing lines {start + 1}-{last} of {len(lines)}{byte_note}. Use offset={last + 1} to continue.]"
            details = {"truncation": result.record()}
        elif limit is not None and end < len(lines):
            output += f"\n\n[{len(lines) - end} more lines in file. Use offset={end + 1} to continue.]"
        return ToolResult(output, metadata=details)
