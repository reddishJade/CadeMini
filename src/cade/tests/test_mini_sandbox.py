"""Cade Mini sandbox 选择逻辑的测试。"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest

from cade.mini import build_sandbox


def test_off_returns_none(tmp_path: Path) -> None:
    assert build_sandbox("off", tmp_path) is None


def test_non_linux_falls_back(tmp_path: Path) -> None:
    if platform.system() == "Linux":
        pytest.skip("Linux may provide bubblewrap")
    assert build_sandbox("workspace", tmp_path) is None
    assert build_sandbox("strict", tmp_path) is None
