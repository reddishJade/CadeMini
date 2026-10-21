"""Cade 技能发现与加载的回归测试。"""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from cade.coding.security.policy import WorkspacePolicy
from cade.coding.skills.context import restore_skill_context
from cade.coding.skills.parsing import parse_skill
from cade.coding.skills.tools import (
    LoadSkillTool,
    discover_skills,
    render_skill_catalog,
)
from cade.coding.tools.registry import build_tools
from cade.harness.execution.local.sandbox import PlatformSandbox
from cade.harness.executor import execute_tool
from cade.harness.permissions import ApprovalResult
from cade.harness.session.log import SessionLog
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock


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

    assert [skill.name for skill in skills.definitions()] == ["greeter"]
    assert skills.definitions()[0].description == "Say hello"
    assert "Say HELLO." in skills.load("greeter").content


def test_project_skill_overrides_user_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path / "user", "review", "user version", "user body")
    _write_skill(tmp_path / ".cade", "review", "project version", "project body")

    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    assert len(skills.definitions()) == 1
    assert skills.definitions()[0].description == "project version"
    assert skills.load("review").content == "project body"


def test_skill_without_frontmatter_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / ".cade" / "skills" / "plain"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "First line description\nMore.", encoding="utf-8"
    )

    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    assert skills.definitions() == ()


def test_catalog_and_load_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "greeter", "Say hello", "Say HELLO.")
    skills = discover_skills(tmp_path, user_dir=tmp_path / "user")

    catalog = render_skill_catalog(skills)
    assert catalog is not None
    assert "<available-skills>" in catalog
    assert "<name>greeter</name>" in catalog

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

    with pytest.raises(ValueError, match="unknown or invalid skill"):
        asyncio.run(
            tool.execute(ToolCallBlock(id="s1", name="skill", args={"name": "missing"}))
        )


def test_policy_allows_skill(tmp_path: Path) -> None:
    policy = WorkspacePolicy(tmp_path)

    assert (
        policy.check(
            ToolCallBlock(id="s1", name="skill", args={"name": "greeter"})
        ).decision
        == "allow"
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "name: BAD\ndescription: test",
        "name: other\ndescription: test",
        "name: valid\ndescription: 123",
        "name: valid\ndescription: test\nmetadata: []",
        "name: valid\ndescription: test\nallowed-tools: []",
    ],
)
def test_invalid_skill_metadata_is_rejected(metadata: str) -> None:
    assert (
        parse_skill(f"---\n{metadata}\n---\nbody", skill_directory_name="valid") is None
    )


def test_body_and_resources_are_loaded_only_when_needed(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "review", "Review code", "Initial body")
    root = tmp_path / ".cade" / "skills" / "review"
    (root / "references").mkdir()
    (root / "references" / "guide.md").write_text(
        "Reference evidence", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "check.py").write_text("print('SCRIPT_OK')", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "data.bin").write_bytes(b"\x00\x01")
    registry = discover_skills(tmp_path, user_dir=tmp_path / "user")
    assert registry.definitions()[0].content == ""
    assert registry.definitions()[0].references == ()
    file = root / "SKILL.md"
    file.write_text(
        file.read_text(encoding="utf-8").replace("Initial body", "Loaded body"),
        encoding="utf-8",
    )
    tool = LoadSkillTool(tmp_path, registry)
    result = tool.run({"name": "review"})
    assert "Loaded body" in result.content
    assert str(root.resolve()) in result.content
    assert "references/guide.md" in result.content
    assert "scripts/check.py" in result.content
    assert "assets/data.bin" in result.content
    assert "Reference evidence" not in result.content
    assert (
        tool.run({"name": "review", "reference": "guide.md"}).content
        == "Reference evidence"
    )
    with pytest.raises(ValueError, match="unsafe reference"):
        tool.run({"name": "review", "reference": "../../outside"})


def test_untrusted_project_skills_are_not_discovered(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "review", "Review code", "body")
    registry = discover_skills(
        tmp_path, user_dir=tmp_path / "user", trust_project_skills=False
    )
    assert registry.definitions() == ()


def test_manual_only_skill_remains_explicitly_available(tmp_path: Path) -> None:
    _write_skill(tmp_path / ".cade", "manual", "Manual only", "Explicit body")
    path = tmp_path / ".cade" / "skills" / "manual" / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "description: Manual only",
            "description: Manual only\ndisable-model-invocation: true",
        ),
        encoding="utf-8",
    )
    registry = discover_skills(tmp_path, user_dir=tmp_path / "user")
    assert render_skill_catalog(registry) is None
    assert (
        "Explicit body"
        in LoadSkillTool(tmp_path, registry).run({"name": "manual"}).content
    )


def test_loaded_skill_survives_compaction_without_duplicate_context(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path / ".cade", "review", "Review code", "Exact workflow")
    registry = discover_skills(tmp_path, user_dir=tmp_path / "user")
    loaded = LoadSkillTool(tmp_path, registry).run({"name": "review"})
    log = SessionLog.create(tmp_path / "sessions")
    log.append("user", {"text": "Review", "source": "user"})
    log.append(
        "assistant",
        {
            "blocks": [
                {
                    "type": "tool_call",
                    "id": "s",
                    "name": "skill",
                    "args": {"name": "review"},
                }
            ]
        },
    )
    log.append(
        "tool_result",
        {
            "call_id": "s",
            "status": "ok",
            "content": loaded.content,
            "artifact": {"tool_metadata": {"skill": "review"}},
        },
    )
    assert restore_skill_context(registry, log.branch(), project(log.branch())) == ()
    retained = log.append("user", {"text": "Continue", "source": "user"})
    log.append(
        "compaction",
        {
            "summary": "Earlier review",
            "first_kept_id": retained.id,
            "tokens_before": 1000,
            "model": "test",
            "provider": "test",
        },
    )
    restored = restore_skill_context(registry, log.branch(), project(log.branch()))
    assert len(restored) == 1
    assert "Exact workflow" in restored[0]


async def test_skill_script_reads_bundled_asset_through_authorized_shell(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _write_skill(
        workspace / ".cade",
        "process",
        "Process bundled data",
        "Run scripts/process.py.",
    )
    root = workspace / ".cade" / "skills" / "process"
    (root / "assets").mkdir()
    (root / "assets" / "input.txt").write_text("asset evidence", encoding="utf-8")
    (root / "scripts").mkdir()
    script = root / "scripts" / "process.py"
    script.write_text(
        "from pathlib import Path\n"
        "data = (Path(__file__).resolve().parents[1] / 'assets/input.txt').read_text()\n"
        "print('SCRIPT_OK:' + data.upper())\n",
        encoding="utf-8",
    )
    registry = discover_skills(workspace, user_dir=tmp_path / "user")
    loaded = LoadSkillTool(workspace, registry).run({"name": "process"})
    assert str(root.resolve()) in loaded.content
    sandbox = PlatformSandbox(
        workspace,
        runtime,
        hidden_paths=(workspace / ".cade",),
        readable_paths=(root,),
    )
    bash = next(
        tool for tool in build_tools(workspace, sandbox=sandbox) if tool.name == "bash"
    )
    command = (
        f"& '{sys.executable}' '{script}'"
        if sys.platform == "win32"
        else f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    )
    approvals: list[str] = []

    def approve(request) -> ApprovalResult:
        approvals.append(request.call.name)
        return ApprovalResult(True, "Run the bundled skill script")

    result = await execute_tool(
        ToolCallBlock(id="script", name="bash", args={"command": command}),
        {"bash": bash},
        policy=WorkspacePolicy(workspace, mode="ask"),
        approve=approve,
    )
    assert not result.result.is_error, result.result.content
    assert "SCRIPT_OK:ASSET EVIDENCE" in result.result.content
    assert approvals == ["bash"]


def test_utf8_reference_is_not_misclassified_at_sample_boundary(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / ".cade", "chinese", "Read Chinese references", "Read the guide."
    )
    references = tmp_path / ".cade" / "skills" / "chinese" / "references"
    references.mkdir()
    content = "中文参考资料" * 200
    (references / "guide.md").write_text(content, encoding="utf-8")
    registry = discover_skills(tmp_path, user_dir=tmp_path / "user")
    tool = LoadSkillTool(tmp_path, registry)
    assert tool.run({"name": "chinese", "reference": "guide.md"}).content == content
