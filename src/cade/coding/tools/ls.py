"""按名称排序目录项，附加目录标记并限制输出。"""

from typing import Any

from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    _is_blocked,
    _resolve_authorized_path,
    number_arg,
)
from cade.coding.tools.search import finish_search
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class LsTool(WorkspaceTool):
    name = "ls"
    description = "List directory contents. Returns entries sorted alphabetically, with '/' suffix for directories. Includes dotfiles. Output is truncated to 500 entries or 50KB (whichever is hit first)."

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "number"}},
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path") or ".")
        _ensure_not_blocked(self.workspace, path)
        if not path.exists():
            raise ValueError(f"Path not found: {path}")
        if not path.is_dir():
            raise ValueError(f"Not a directory: {path}")
        limit = int(number_arg(args, "limit", 500))
        entries = sorted(path.iterdir(), key=lambda entry: entry.name.lower())
        results: list[str] = []
        reached = False
        for entry in entries:
            if _is_blocked(self.workspace, entry):
                continue
            if len(results) >= limit:
                reached = True
                break
            try:
                entry.stat()
            except OSError:
                continue
            results.append(entry.name + ("/" if entry.is_dir() else ""))
        if not results:
            return ToolResult("(empty directory)")
        notices = (
            [f"{limit} entries limit reached. Use limit={limit * 2} for more"]
            if reached
            else []
        )
        return finish_search(
            "\n".join(results), notices, {"entryLimitReached": limit} if reached else {}
        )
