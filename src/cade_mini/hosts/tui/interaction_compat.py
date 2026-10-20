"""TUI 需要的文件引用、技能、Git 与快捷执行辅助。"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, cast

from cade_mini.core.types import ToolCallBlock
from cade_mini.tools.workspace import resolve_workspace_path

from .compat import file_reference_event
from .settings import current_effort_options, current_model_options

FILE_REF_PATTERN = re.compile(r"(?<!\S)@([^\s]+)")
_SKILL_INVOCATION_PATTERN = re.compile(
    r"^\$([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s+(.*))?$",
    re.DOTALL,
)
CUSTOM_OPTION_LABEL = "Custom answer"


@dataclass(frozen=True)
class FileReference:
    path: str
    status: str
    content: str = ""
    error: str = ""


@dataclass(frozen=True)
class ExplicitSkillActivationResult:
    name: str
    status: str
    message: str


def _read_project_text_file(project_root: Path, raw_path: str) -> str:
    path = resolve_workspace_path(project_root, raw_path)
    if not path.is_file():
        raise ValueError(f"File does not exist: {raw_path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > 50_000:
        content = content[:50_000] + "\n[file truncated]"
    return content


def expand_file_references(
    text: str, project_root: Path
) -> tuple[str, list[FileReference]]:
    """把用户输入中的 @file 引用展开为带内容的 XML 块。"""
    references: list[FileReference] = []
    for match in FILE_REF_PATTERN.finditer(text):
        raw_path = match.group(1).rstrip(".,;:)")
        if not raw_path:
            continue
        try:
            content = _read_project_text_file(project_root, raw_path)
            references.append(
                FileReference(path=raw_path, status="ok", content=content)
            )
        except (OSError, UnicodeError, ValueError) as exc:
            references.append(
                FileReference(path=raw_path, status="error", error=str(exc))
            )
    if not references:
        return text, []

    blocks: list[str] = []
    for reference in references:
        if reference.status == "ok":
            blocks.append(
                f'<file-reference path="{escape(reference.path, quote=True)}">\n'
                f"{reference.content}\n"
                "</file-reference>"
            )
        else:
            blocks.append(
                f'<file-reference path="{escape(reference.path, quote=True)}" '
                f'error="{escape(reference.error, quote=True)}" />'
            )
    expanded = "\n\n".join(blocks) + "\n\n<user-message>\n" + text + "\n</user-message>"
    return expanded, references


def git_branch_name(project_root: Path) -> str | None:
    """返回当前 Git 分支名；目录不是工作区时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "branch", "--show-current"],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def parse_skill_invocation(text: str) -> tuple[str, str] | None:
    """解析行首 `$skill-name` 及其后续任务。"""
    match = _SKILL_INVOCATION_PATTERN.fullmatch(text.strip())
    if match is None:
        return None
    return match.group(1), (match.group(2) or "").strip()


def available_skill_names(app: object) -> tuple[str, ...]:
    """从 InteractionService 读取可显式激活的技能名称。"""
    service = getattr(app, "service", None)
    skills = getattr(service, "skills", ())
    return tuple(skill.name for skill in skills)


def activate_skill(
    app: object, skill_name: str, mode: str | None = None
) -> ExplicitSkillActivationResult:
    """标记待注入的技能；实际正文在下一轮请求前展开。"""
    del mode
    service = getattr(app, "service", None)
    skills = getattr(service, "skills", ())
    skill = next((item for item in skills if item.name == skill_name), None)
    if skill is None:
        return ExplicitSkillActivationResult(
            skill_name, "disabled", f"Unknown skill: {skill_name}"
        )
    cast(Any, app)._pending_skill = skill_name
    return ExplicitSkillActivationResult(
        skill_name, "activated", f"Skill activated: {skill_name}"
    )


def run_shell_shortcut(command: str, app: object) -> str:
    """执行 `!command` 快捷命令，返回终端输出。"""
    import asyncio
    import uuid

    shell_command = command[1:].strip()
    if not shell_command:
        return "usage: !COMMAND"
    service = getattr(app, "service", None)
    tool = next(
        (
            item
            for item in getattr(service, "tools", ())
            if item.name in {"bash", "shell"}
        ),
        None,
    )
    if tool is None:
        return "Shell tool is not available."

    async def execute() -> str:
        result = await tool.execute(
            ToolCallBlock(
                id=uuid.uuid4().hex,
                name=tool.name,
                args={"command": shell_command},
            ),
            None,
        )
        return result.content

    try:
        return asyncio.run(execute())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"Shell command failed: {exc}"


__all__ = [
    "CUSTOM_OPTION_LABEL",
    "ExplicitSkillActivationResult",
    "FileReference",
    "activate_skill",
    "available_skill_names",
    "current_effort_options",
    "current_model_options",
    "expand_file_references",
    "file_reference_event",
    "git_branch_name",
    "parse_skill_invocation",
    "run_shell_shortcut",
]
