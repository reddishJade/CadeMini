"""工作区文件匹配与内容搜索。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cade.coding.tools.base import (
    MAX_FILE_BYTES,
    MAX_GLOB_RESULTS,
    MAX_GREP_RESULTS,
    WorkspaceTool,
    _is_blocked,
    display_path,
    int_arg,
    looks_binary,
    text_arg,
)
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class GlobTool(WorkspaceTool):
    name = "glob"
    description = (
        "List files matching a glob pattern relative to the workspace. "
        "Use ** for recursive matches."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        pattern = text_arg(args, "pattern")
        _validate_pattern(pattern)
        limit = int_arg(args, "limit", 200, 1, MAX_GLOB_RESULTS)
        matches: list[str] = []
        for path in self.workspace.glob(pattern):
            if len(matches) >= limit:
                break
            if not path.is_file() or path.is_symlink():
                continue
            if _is_blocked(self.workspace, path):
                continue
            matches.append(display_path(self.workspace, path))
        if not matches:
            return ToolResult(content="No files matched.")
        return ToolResult(content="\n".join(sorted(matches)))


class GrepTool(WorkspaceTool):
    name = "grep"
    description = (
        "Search file contents with a regular expression. Returns path:line:text "
        "matches from text files in the workspace."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        pattern = text_arg(args, "pattern")
        glob_pattern = text_arg(args, "glob", "**/*")
        _validate_pattern(glob_pattern)
        limit = int_arg(args, "limit", 100, 1, MAX_GREP_RESULTS)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        matches: list[str] = []
        paths = (
            self.workspace.glob(glob_pattern)
            if "**" in glob_pattern
            else self.workspace.rglob(glob_pattern)
        )
        for path in paths:
            if len(matches) >= limit:
                break
            if not path.is_file() or path.is_symlink():
                continue
            if _is_blocked(self.workspace, path):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if looks_binary(data):
                continue
            text = data.decode("utf-8", errors="replace")
            relative = display_path(self.workspace, path)
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{relative}:{number}: {line[:300]}")
                    if len(matches) >= limit:
                        break
        if not matches:
            return ToolResult(content="No matches.")
        return ToolResult(content="\n".join(matches))


def _validate_pattern(pattern: str) -> None:
    if not pattern.strip():
        raise ValueError("pattern must not be empty")
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("pattern must stay inside the workspace")
