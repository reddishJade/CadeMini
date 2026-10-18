"""进程包装协议；Mini 默认不启用操作系统沙箱。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SandboxedCommand:
    """可交给进程启动器的包装命令。"""

    argv: tuple[str, ...]
    cwd: Path
    finalize: Callable[[], str | None] | None = None


class CommandSandbox(Protocol):
    """在进程启动前包装命令。"""

    def wrap(self, argv: list[str], cwd: Path) -> SandboxedCommand: ...
