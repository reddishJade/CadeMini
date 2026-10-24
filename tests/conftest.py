"""按宿主平台选择真实 shell，并构造对应的 Python 命令。"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Sequence

import pytest

from cade.coding.tools.bash import BashTool, PowerShellTool


@pytest.fixture
def shell_tool_class() -> type[BashTool]:
    return PowerShellTool if sys.platform == "win32" else BashTool


@pytest.fixture
def python_command() -> Callable[[Sequence[str]], str]:
    def command(args: Sequence[str]) -> str:
        argv = [sys.executable, *args]
        if sys.platform == "win32":
            return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
        return shlex.join(argv)

    return command
