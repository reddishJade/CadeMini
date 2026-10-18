"""Cade Mini 项目指令加载的回归测试。"""

from __future__ import annotations

from pathlib import Path

from cade.mini import load_project_instructions


def test_agents_override_wins(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("base rules", encoding="utf-8")
    (tmp_path / "AGENTS.override.md").write_text(
        "override rules",
        encoding="utf-8",
    )

    text = load_project_instructions(tmp_path)

    assert text is not None
    assert "override rules" in text
    assert "base rules" not in text
    assert 'source="AGENTS.override.md"' in text


def test_falls_back_to_claude(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")

    text = load_project_instructions(tmp_path)

    assert text is not None
    assert "claude rules" in text
    assert 'source="CLAUDE.md"' in text


def test_no_instruction_file(tmp_path: Path) -> None:
    assert load_project_instructions(tmp_path) is None


def test_large_instructions_are_truncated(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * (70 * 1024), encoding="utf-8")

    text = load_project_instructions(tmp_path)

    assert text is not None
    assert "truncated" in text
    assert len(text) < 70 * 1024 + 200
