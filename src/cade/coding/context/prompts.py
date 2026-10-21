"""可组合的系统提示模块。"""

from __future__ import annotations

import platform
from collections.abc import Callable, Sequence
from pathlib import Path

type ModuleRenderer = Callable[[Path], str]

DEFAULT_MODULES: tuple[str, ...] = (
    "identity",
    "tool_discipline",
    "working_note",
    "environment",
)


def _identity(project_root: Path) -> str:
    del project_root
    return (
        "You are Cade, a coding agent working in a local workspace. "
        "Be concise and direct."
    )


def _tool_discipline(project_root: Path) -> str:
    del project_root
    return (
        "Use the provided tools to inspect and modify files. Read before editing. "
        "Prefer small, verifiable changes and run relevant checks when useful. "
        "Use the history tool for evidence that is no longer in the request. "
        "Never claim a change you did not make."
    )


def _working_note(project_root: Path) -> str:
    del project_root
    return (
        "For long-running work, keep durable state in NOTE.md at the workspace "
        "root. NOTE.md is loaded into context on each task."
    )


def _environment(project_root: Path) -> str:
    return (
        f"Working directory: {project_root}\n"
        f"Platform: {platform.system()} {platform.release()}\n"
        f"Python: {platform.python_version()}"
    )


_MODULES: dict[str, ModuleRenderer] = {
    "identity": _identity,
    "tool_discipline": _tool_discipline,
    "working_note": _working_note,
    "environment": _environment,
}


def render_system_prompt(
    project_root: Path,
    modules: Sequence[str] | None = None,
) -> str:
    """按名称组合系统提示；未知模块或空列表直接报错。"""
    names = tuple(modules) if modules is not None else DEFAULT_MODULES
    if not names:
        raise ValueError("at least one prompt module is required")
    unknown = [name for name in names if name not in _MODULES]
    if unknown:
        raise ValueError(
            f"unknown prompt module(s): {', '.join(unknown)}; "
            f"available: {', '.join(_MODULES)}"
        )
    root = project_root.resolve()
    return "\n\n".join(_MODULES[name](root) for name in names)
