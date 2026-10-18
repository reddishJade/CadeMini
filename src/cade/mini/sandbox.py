"""可选的 OS sandbox 接入；非 Linux 主机回退为路径与命令策略。"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Literal

from cade.harness.execution_env import (
    CommandSandbox,
    LinuxBubblewrapSandbox,
    NetworkAccess,
    SandboxMode,
    SandboxPolicy,
    SandboxUnavailableError,
)

type MiniSandboxMode = Literal["off", "workspace", "strict"]


def build_sandbox(
    mode: MiniSandboxMode,
    workspace: Path,
) -> CommandSandbox | None:
    """Linux 上按模式构造 bubblewrap；其他主机或不可用时返回 None。"""
    if mode == "off" or platform.system() != "Linux":
        return None
    policy_mode = (
        SandboxMode.WORKSPACE_WRITE if mode == "workspace" else SandboxMode.READ_ONLY
    )
    policy = SandboxPolicy(
        project_root=workspace.resolve(),
        mode=policy_mode,
        network_access=NetworkAccess.DENY,
    )
    try:
        return LinuxBubblewrapSandbox(policy)
    except (OSError, SandboxUnavailableError):
        return None
