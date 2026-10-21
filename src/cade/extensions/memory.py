"""跨任务的持久记忆：读取 MEMORY.md，并通过工具追加事实。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from cade.core.loop import CancellationToken, ToolResult
from cade.core.types import ToolCallBlock
from cade.tools.workspace import _text_arg

MEMORY_NAME = "MEMORY.md"
MAX_MEMORY_BYTES = 32 * 1024
MAX_WRITE_BYTES = 64 * 1024


def load_memory(project_root: Path) -> str | None:
    """返回 MEMORY.md 的请求级上下文块；不存在或为空时返回 None。"""
    path = project_root.resolve() / MEMORY_NAME
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    truncated = len(data) > MAX_MEMORY_BYTES
    text = data[:MAX_MEMORY_BYTES].decode("utf-8", errors="replace").strip()
    if not text:
        return None
    marker = "\n[memory truncated]" if truncated else ""
    return f'<memory source="{MEMORY_NAME}">\n{text}{marker}\n</memory>'


class MemoryTool:
    """把一个长期事实追加到工作区 MEMORY.md。"""

    name = "memory"
    description = (
        "Append one durable fact to MEMORY.md so future tasks can use it. "
        "Use it for stable preferences, decisions, and project conventions."
    )
    parameters: dict[str, Any]

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.path = self.workspace / MEMORY_NAME
        self.parameters = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        del signal
        text = _text_arg(call.args, "text").strip()
        if not text:
            raise ValueError("memory text must not be empty")
        await asyncio.to_thread(self._append, text)
        return ToolResult(
            content=f"Remembered in {MEMORY_NAME}: {text}",
            metadata={"path": MEMORY_NAME},
        )

    def _append(self, text: str) -> None:
        existing = self.path.stat().st_size if self.path.exists() else 0
        line = f"- {text}\n"
        if existing + len(line.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ValueError(f"{MEMORY_NAME} is full; remove stale entries first")
        with self.path.open("a", encoding="utf-8") as handle:
            if existing == 0:
                handle.write("# Memory\n\n")
            handle.write(line)
