"""Cade Mini 技能发现与加载的回归测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cade.mini.core.types import ToolCallBlock
from cade.mini.extensions.skills import (
    LoadSkillTool,
    discover_skills,
    render_skill_catalog,
)
from cade.mini.safety.policy import WorkspacePolicy


def _write_skill(
    root: Path,
    name: str,
    description: str,
    body: str,
) -> None:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_discover_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "greeter", "Say hello", "Say HELLO.")

    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    assert [skill.name for skill in skills] == ["greeter"]
    assert skills[0].description == "Say hello"
    assert "Say HELLO." in skills[0].body


def test_project_skill_overrides_user_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path / "user", "review", "user version", "user body")
    _write_skill(tmp_path / ".cade", "review", "project version", "project body")

    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    assert len(skills) == 1
    assert skills[0].description == "project version"
    assert skills[0].body == "project body"


def test_skill_without_frontmatter_uses_directory_name(tmp_path: Path) -> None:
    directory = tmp_path / ".cade" / "skills" / "plain"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "First line description\nMore.", encoding="utf-8"
    )

    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    assert [skill.name for skill in skills] == ["plain"]
    assert skills[0].description == "First line description"


def test_catalog_and_load_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "greeter", "Say hello", "Say HELLO.")
    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    catalog = render_skill_catalog(skills)
    assert catalog is not None
    assert "<available-skills>" in catalog
    assert "greeter: Say hello" in catalog

    tool = LoadSkillTool(tmp_path, skills)
    result = asyncio.run(
        tool.execute(ToolCallBlock(id="s1", name="skill", args={"name": "greeter"}))
    )
    assert "Say HELLO." in result.content
    assert result.metadata == {"skill": "greeter"}


def test_unknown_skill_raises(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "greeter", "Say hello", "Say HELLO.")
    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")
    tool = LoadSkillTool(tmp_path, skills)

    with pytest.raises(ValueError, match="unknown skill"):
        asyncio.run(
            tool.execute(ToolCallBlock(id="s1", name="skill", args={"name": "missing"}))
        )


def test_policy_allows_skill(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path)

    assert (
        policy.check(ToolCallBlock(id="s1", name="skill", args={"name": "greeter"}))
        == "allow"
    )
