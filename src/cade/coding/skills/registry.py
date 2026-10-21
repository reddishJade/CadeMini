"""技能元数据、正文快照与资源索引。"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from cade.coding.skills.discovery import (
    load_reference_text,
    scan_skill_references,
    scan_skill_resources,
    source_for_priority,
)
from cade.coding.skills.models import SkillDef, SkillSummary
from cade.coding.skills.parsing import (
    frontmatter_optional_string,
    parse_skill,
)

logger = logging.getLogger(__name__)


class SkillRegistry:
    """技能定义快照与资源索引。

    使用方式：
        registry = SkillRegistry()
        registry.discover(search_dirs)
        summaries = registry.list_summaries()
        skill = registry.load("code-review")
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillDef] = {}
        self._ref_paths: dict[str, dict[str, Path]] = {}

    def discover(
        self,
        search_dirs: list[tuple[Path, int]],
    ) -> None:
        """扫描目录查找 SKILL.md 文件，缓存元数据。

        相同 name 按搜索路径优先级 first-wins。重复时记录警告。
        跳过 malformed frontmatter 或缺少必填字段的技能。
        """
        all_dirs = sorted(search_dirs, key=lambda x: x[1])
        for search_dir, priority in all_dirs:
            if not search_dir.is_dir():
                continue
            source = source_for_priority(priority)
            try:
                for file_path in sorted(search_dir.rglob("SKILL.md")):
                    try:
                        with file_path.open(encoding="utf-8") as stream:
                            lines = [stream.readline(4096)]
                            for _ in range(256):
                                line = stream.readline(4096)
                                lines.append(line)
                                if line.rstrip() == "---":
                                    break
                            text = "".join(lines)
                    except (OSError, UnicodeError):
                        msg = f"Failed to read {file_path}; skipping"
                        logger.warning(msg)
                        continue
                    parsed = parse_skill(
                        text,
                        skill_directory_name=file_path.parent.name,
                        source=file_path,
                    )
                    if parsed is None:
                        continue
                    frontmatter, _body = parsed
                    name = str(frontmatter["name"])
                    if name in self._skills:
                        logger.warning(
                            "Duplicate skill %r from %s; keeping %s",
                            name,
                            file_path,
                            self._skills[name].file_path,
                        )
                        continue
                    raw_disable = frontmatter.get("disable_model_invocation", False)

                    self._skills[name] = SkillDef(
                        name=name,
                        description=str(frontmatter["description"]),
                        disable_model_invocation=bool(raw_disable),
                        source=source,
                        compatibility=frontmatter_optional_string(
                            frontmatter, "compatibility"
                        ),
                        allowed_tools=frontmatter_optional_string(
                            frontmatter, "allowed-tools"
                        ),
                        file_path=file_path,
                        content="",
                    )
            except OSError:
                logger.exception("SkillRegistry: failed to scan %s", search_dir)
                continue

    def list_summaries(self) -> list[SkillSummary]:
        """返回所有未被 disable_model_invocation 禁止的可见技能摘要。"""
        return [
            skill.to_summary()
            for skill in self._skills.values()
            if not skill.disable_model_invocation
        ]

    def available_names(self) -> tuple[str, ...]:
        """返回可向模型披露和激活的技能名称。"""
        return tuple(sorted(summary.name for summary in self.list_summaries()))

    def contains(self, skill_name: str) -> bool:
        """判断技能名称是否已被发现。"""
        return skill_name in self._skills

    def is_available(self, skill_name: str) -> bool:
        """判断技能是否允许用户或模型显式激活。"""
        skill = self._skills.get(skill_name)
        return skill is not None

    def load(self, skill_name: str) -> SkillDef | None:
        """激活时读取正文和资源清单；未知名称返回 None。"""
        skill = self._skills.get(skill_name)
        if skill is None:
            return None
        if skill.content:
            return skill
        try:
            text = skill.file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            logger.warning("Cannot load skill %s: %s", skill_name, exc)
            return None
        parsed = parse_skill(
            text,
            skill_directory_name=skill.file_path.parent.name,
            source=skill.file_path,
        )
        if parsed is None or parsed[0]["name"] != skill_name:
            return None
        refs, paths = scan_skill_references(skill.file_path.parent)
        self._ref_paths[skill_name] = paths
        skill = replace(
            skill,
            content=parsed[1],
            references=tuple(refs),
            scripts=scan_skill_resources(skill.file_path.parent, "scripts"),
            assets=scan_skill_resources(skill.file_path.parent, "assets"),
        )
        self._skills[skill_name] = skill
        return skill

    def definitions(self) -> tuple[SkillDef, ...]:
        """返回包含手动触发项的定义目录，不加载正文。"""
        return tuple(self._skills.values())

    def load_reference(self, skill_name: str, ref_name: str) -> str | None:
        """加载指定技能中的引用文件内容。

        仅允许加载在 discover() 阶段发现且未跳过的引用。
        返回内容受 _REFERENCE_MAX_BYTES 截断保护。
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return None

        ref_map = self._ref_paths.get(skill_name, {})
        ref_path = ref_map.get(ref_name)
        if ref_path is None:
            return None

        root = skill.file_path.parent.resolve()
        if ref_path.is_symlink() or not ref_path.resolve().is_relative_to(root):
            return None
        content, truncated = load_reference_text(ref_path)
        if content is not None and truncated:
            content += "\n[reference truncated at 50 KiB]"
        return content
