"""读取工作区 NOTE.md，作为请求级工作状态。"""

from __future__ import annotations

from pathlib import Path

NOTE_NAME = "NOTE.md"
MAX_NOTE_BYTES = 32 * 1024
_TRUNCATION_MARKER = "\n[working note truncated]"


def load_working_note(project_root: Path) -> str | None:
    """返回 NOTE.md 的请求级上下文块；不存在或为空时返回 None。"""
    path = project_root.resolve() / NOTE_NAME
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    truncated = len(data) > MAX_NOTE_BYTES
    text = data[:MAX_NOTE_BYTES].decode("utf-8", errors="replace").strip()
    if not text:
        return None
    suffix = _TRUNCATION_MARKER if truncated else ""
    return f'<working-note source="{NOTE_NAME}">\n{text}{suffix}\n</working-note>'
