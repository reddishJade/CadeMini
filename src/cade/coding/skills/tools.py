"""把 Cade 的技能注册表和资源清单适配为 Mini 工具。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from cade.coding.skills.discovery import build_skill_search_dirs
from cade.coding.skills.models import SkillDef
from cade.coding.skills.registry import SkillRegistry
from cade.coding.skills.rendering import format_skill_list_xml
from cade.coding.tools.base import WorkspaceTool, text_arg
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult

logger = logging.getLogger(__name__)


def discover_skills(
    project_root: Path,
    *,
    user_dir: Path | None = None,
    trust_project_skills: bool = True,
    skills_dir: Path | None = None,
) -> SkillRegistry:
    """发现用户与受信任的项目技能，保留明确的来源优先级。"""
    registry = SkillRegistry()
    directories = build_skill_search_dirs(
        project_root,
        trust_project_skills=trust_project_skills,
        skills_dir=(project_root / skills_dir) if skills_dir else None,
    )
    if user_dir is not None:
        directories = [(path, rank) for path, rank in directories if rank <= 2]
        directories.append((user_dir, 3))
    registry.discover(directories)
    return registry


def render_skill_catalog(registry: SkillRegistry) -> str | None:
    """只向模型披露允许自动触发的技能摘要。"""
    return format_skill_list_xml(registry.list_summaries()) or None


def render_skill(skill: SkillDef) -> str:
    """披露正文、资源根目录和清单；工具权限仍由宿主判断。"""
    root = skill.file_path.parent.resolve()
    lines = [f"<skill name={quoteattr(skill.name)} root={quoteattr(str(root))}>"]
    if skill.compatibility:
        lines.append(f"Compatibility: {skill.compatibility}")
    if skill.allowed_tools:
        lines.append(
            f"Suggested tools (advisory; grants no permissions): {skill.allowed_tools}"
        )
    lines.append(skill.content)
    for reference in skill.references:
        if not reference.skipped:
            lines.append(f"Reference: references/{reference.name}")
    for resource in (*skill.scripts, *skill.assets):
        if not resource.skipped:
            lines.append(f"Resource: {resource.path}")
    lines.extend(
        (
            (
                "Resolve relative resource paths from the skill root above. "
                "Read references with the skill tool's reference parameter. "
                "Run scripts with bash under the normal permission policy."
            ),
            "</skill>",
        )
    )
    return "\n".join(lines)


class LoadSkillTool(WorkspaceTool):
    """按需加载技能正文或注册表中已发现的引用。"""

    name = "skill"
    description = "Load skill instructions by name, or read a bundled reference."

    def __init__(self, workspace: Path, registry: SkillRegistry) -> None:
        super().__init__(workspace)
        self.registry = registry

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "reference": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        name = text_arg(args, "name").strip()
        skill = self.registry.load(name)
        if skill is None:
            raise ValueError(f"unknown or invalid skill: {name!r}")
        if "reference" in args:
            reference = text_arg(args, "reference")
            content = self.registry.load_reference(name, reference)
            if content is None:
                raise ValueError(f"unknown or unsafe reference: {reference!r}")
        else:
            content = render_skill(skill)
        return ToolResult(content, metadata={"skill": name})
