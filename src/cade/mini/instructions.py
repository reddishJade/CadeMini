"""加载工作区的项目指令文件。"""

from __future__ import annotations

from pathlib import Path

MAX_INSTRUCTION_BYTES = 64 * 1024
_INSTRUCTION_FILES = (
    "AGENTS.override.md",
    "AGENTS.md",
    "agents.md",
    "CLAUDE.md",
    "claude.md",
)
_TRUNCATION_MARKER = "\n[project instructions truncated]"


def load_project_instructions(project_root: Path) -> str | None:
    """返回根目录第一份指令文件，打包成请求级上下文块。"""
    root = project_root.resolve()
    for name in _INSTRUCTION_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        truncated = len(data) > MAX_INSTRUCTION_BYTES
        text = data[:MAX_INSTRUCTION_BYTES].decode("utf-8", errors="replace").strip()
        if not text:
            return None
        suffix = _TRUNCATION_MARKER if truncated else ""
        return (
            f'<project-instructions source="{name}">\n'
            f"{text}{suffix}\n"
            "</project-instructions>"
        )
    return None
