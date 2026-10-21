"""Cade NOTE.md 工作状态的回归测试。"""

from __future__ import annotations

from pathlib import Path

from cade.coding.context.note import load_working_note


def test_loads_working_note(tmp_path: Path) -> None:
    (tmp_path / "NOTE.md").write_text("## Next\nfinish the task", encoding="utf-8")

    note = load_working_note(tmp_path)

    assert note is not None
    assert "finish the task" in note
    assert 'source="NOTE.md"' in note


def test_missing_or_empty_note(tmp_path: Path) -> None:
    assert load_working_note(tmp_path) is None
    (tmp_path / "NOTE.md").write_text("   \n", encoding="utf-8")
    assert load_working_note(tmp_path) is None


def test_large_note_is_truncated(tmp_path: Path) -> None:
    (tmp_path / "NOTE.md").write_text("x" * (40 * 1024), encoding="utf-8")

    note = load_working_note(tmp_path)

    assert note is not None
    assert "truncated" in note
    assert len(note) < 40 * 1024 + 200
