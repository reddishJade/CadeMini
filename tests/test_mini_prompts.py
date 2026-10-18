"""Cade Mini 系统提示模块的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.app.prompts import DEFAULT_MODULES, render_system_prompt


def test_default_prompt_contains_modules(tmp_path: Path) -> None:
    text = render_system_prompt(tmp_path)

    assert "Cade Mini" in text
    assert "NOTE.md" in text
    assert "Platform:" in text
    assert str(tmp_path.resolve()) in text


def test_custom_modules(tmp_path: Path) -> None:
    text = render_system_prompt(tmp_path, ["identity"])

    assert "Cade Mini" in text
    assert "NOTE.md" not in text
    assert "Platform:" not in text


def test_unknown_module_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown prompt module"):
        render_system_prompt(tmp_path, ["missing"])


def test_empty_module_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        render_system_prompt(tmp_path, [])


def test_default_modules_are_stable() -> None:
    assert DEFAULT_MODULES == (
        "identity",
        "tool_discipline",
        "working_note",
        "environment",
    )
