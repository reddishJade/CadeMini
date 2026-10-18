"""发现并按需加载项目或用户级 SKILL.md。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .loop import ToolResult
from .tools import WorkspaceTool, _text_arg
from .types import JsonValue

MAX_SKILL_BYTES = 64 * 1024
MAX_SKILLS = 50
MAX_DESCRIPTION_CHARS = 200
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_USER_DIR = Path.home() / ".cade" / "skills"


@dataclass(frozen=True)
class Skill:
    """一个已发现的技能；正文按需加载。"""

    name: str
    description: str
    body: str
    path: Path


def discover_skills(
    project_root: Path,
    *,
    user_dir: Path | None = None,
) -> tuple[Skill, ...]:
    """按用户级 → .cade → .cade-mini 的顺序发现；项目级覆盖用户级。"""
    found: dict[str, Skill] = {}
    roots = [
        user_dir if user_dir is not None else _USER_DIR,
        project_root / ".cade" / "skills",
        project_root / ".cade-mini" / "skills",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            skill = _parse_skill(path)
            if skill is not None:
                found[skill.name] = skill
    return tuple(list(found.values())[:MAX_SKILLS])


def render_skill_catalog(skills: tuple[Skill, ...]) -> str | None:
    """把技能名称与描述渲染成请求级目录。"""
    if not skills:
        return None
    lines = ["<available-skills>"]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    lines.append("</available-skills>")
    return "\n".join(lines)


class LoadSkillTool(WorkspaceTool):
    """按名称加载一个技能的完整说明。"""

    name = "skill"
    description = (
        "Load the full instructions of an available skill by name. "
        "Use the catalog in the system context to choose a skill."
    )

    def __init__(self, workspace: Path, skills: tuple[Skill, ...]) -> None:
        super().__init__(workspace)
        self.skills = skills

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        name = _text_arg(args, "name").strip()
        skill = next((item for item in self.skills if item.name == name), None)
        if skill is None:
            available = ", ".join(item.name for item in self.skills) or "(none)"
            raise ValueError(f"unknown skill {name!r}; available: {available}")
        return ToolResult(
            content=f'<skill name="{skill.name}">\n{skill.body}\n</skill>',
            metadata={"skill": skill.name},
        )


def _parse_skill(path: Path) -> Skill | None:
    try:
        data = path.read_bytes()[:MAX_SKILL_BYTES]
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    metadata: dict[str, object] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                loaded = yaml.safe_load(parts[1])
            except yaml.YAMLError:
                loaded = None
            if isinstance(loaded, dict):
                metadata = loaded
            body = parts[2]
    name = str(metadata.get("name") or path.parent.name).strip()
    if not _SKILL_NAME.fullmatch(name):
        return None
    description = str(metadata.get("description") or "").strip()
    if not description:
        description = next(
            (line.strip() for line in body.splitlines() if line.strip()),
            name,
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + "..."
    body = body.strip()
    if not body:
        return None
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
    )
