"""Cade Mini shell 命令分类的回归测试。"""

from __future__ import annotations

import pytest

from cade.mini import classify_command


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
