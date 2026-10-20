"""Cade Mini shell 命令分类的回归测试。"""

from __future__ import annotations

import pytest

from cade_mini.safety.shell import classify_command, mutation_targets


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls", "read_only"),
        ("cat README.md", "read_only"),
        ("rg TODO", "read_only"),
        ("git status", "read_only"),
        ("git diff HEAD", "read_only"),
        ("type README.md", "read_only"),
        ("find . -delete", "mutating"),
        ("find . -fprint found.txt", "mutating"),
        ("git diff --output=changed.txt", "mutating"),
        ("rg --pre=python text .", "mutating"),
        ("ls\nrm a.txt", "unknown"),
        ("rm a.txt", "mutating"),
        ("git commit -m x", "mutating"),
        ("python -c 'print(1)'", "mutating"),
        ("echo hi > a.txt", "unknown"),
        ("ls | grep a", "unknown"),
        ("ls; rm a", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_command(command: str, expected: str) -> None:
    assert classify_command(command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm a.txt", ("a.txt",)),
        ("echo hi > a.txt", ("a.txt",)),
        ("cp a.txt b.txt", ("b.txt",)),
        ("ls", ()),
        ("rm a; rm b", ()),
    ],
)
def test_mutation_targets(
    command: str,
    expected: tuple[str, ...],
) -> None:
    assert mutation_targets(command) == expected
