"""从会话日志精确检索被压缩或被裁剪的历史证据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cade.mini.core.log import SessionLog
from cade.mini.core.loop import ToolResult
from cade.mini.core.types import JsonValue, LogEntry
from cade.mini.tools.workspace import WorkspaceTool, _int_arg

_MAX_EXACT_CHARS = 64_000
_MAX_SNIPPET_CHARS = 200


class HistoryTool(WorkspaceTool):
    """按关键词、entry id 或 call id 读取原始事实。"""

    name = "history"
    description = (
        "Search or read the durable session history. Use query to find old "
        "evidence, entry_id for an exact entry, or call_id for the exact tool "
        "result behind a pruned or compacted call."
    )

    def __init__(self, workspace: Path, log: SessionLog) -> None:
        super().__init__(workspace)
        self.log = log

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entry_id": {"type": "string"},
                "call_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        entries = self.log.branch()
        entry_id = _optional_text(args, "entry_id")
        if entry_id is not None:
            return self._exact_entry(entries, entry_id)
        call_id = _optional_text(args, "call_id")
        if call_id is not None:
            return self._exact_call(entries, call_id)
        query = _optional_text(args, "query")
        if not query or not query.strip():
            raise ValueError("query, entry_id, or call_id is required")
        limit = _int_arg(args, "limit", 5, 1, 20)
        matches: list[str] = []
        needle = query.strip().casefold()
        for entry in entries:
            text = _entry_text(entry)
            if needle not in text.casefold():
                continue
            matches.append(f"{entry.id} [{entry.type}] {_snippet(text)}")
            if len(matches) >= limit:
                break
        if not matches:
            return ToolResult(content="No matching history entries.")
        return ToolResult(
            content=(
                "Matching entries (use entry_id or call_id for exact content):\n"
                + "\n".join(matches)
            )
        )

    def _exact_entry(
        self,
        entries: list[LogEntry],
        entry_id: str,
    ) -> ToolResult:
        entry = next((item for item in entries if item.id == entry_id), None)
        if entry is None:
            raise ValueError(f"history entry not found: {entry_id}")
        text = _entry_text(entry)
        return ToolResult(
            content=(f"{entry.id} [{entry.type}] {entry.ts}\n{_bound(text)}"),
            metadata={"entry_id": entry.id, "type": entry.type},
        )

    def _exact_call(
        self,
        entries: list[LogEntry],
        call_id: str,
    ) -> ToolResult:
        for entry in entries:
            if entry.type != "tool_result":
                continue
            if entry.data.get("call_id") != call_id:
                continue
            content = entry.data.get("content")
            text = content if isinstance(content, str) else ""
            return ToolResult(
                content=(f"tool_result {entry.id} call_id={call_id}\n{_bound(text)}"),
                metadata={"entry_id": entry.id, "call_id": call_id},
            )
        raise ValueError(f"tool result not found for call_id: {call_id}")


def _optional_text(
    args: dict[str, JsonValue],
    key: str,
) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _entry_text(entry: LogEntry) -> str:
    data = entry.data
    if entry.type == "tool_result":
        content = data.get("content")
        return content if isinstance(content, str) else ""
    if entry.type in {"user", "system"}:
        text = data.get("text")
        return text if isinstance(text, str) else ""
    if entry.type == "compaction":
        summary = data.get("summary")
        return summary if isinstance(summary, str) else ""
    if entry.type == "window":
        handoff = data.get("handoff")
        return handoff if isinstance(handoff, str) else ""
    if entry.type == "assistant":
        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            return ""
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                parts.append(str(block["text"]))
            elif kind == "tool_call":
                parts.append(
                    f"tool_call {block.get('name', '')} {block.get('args', {})}"
                )
        return "\n".join(parts)
    return ""


def _snippet(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_SNIPPET_CHARS:
        return collapsed
    return collapsed[:_MAX_SNIPPET_CHARS] + "..."


def _bound(text: str) -> str:
    if len(text) <= _MAX_EXACT_CHARS:
        return text
    head = text[: _MAX_EXACT_CHARS // 2]
    tail = text[-(_MAX_EXACT_CHARS - _MAX_EXACT_CHARS // 2) :]
    return f"{head}\n[...exact content truncated...]\n{tail}"
