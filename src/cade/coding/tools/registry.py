"""组装内置工作区工具。"""

from __future__ import annotations

from pathlib import Path

from cade.coding.tools.base import (
    WorkspaceTool,
)
from cade.coding.tools.bash import BashTool, PowerShellTool
from cade.coding.tools.edit import EditTool
from cade.coding.tools.read import ReadTool
from cade.coding.tools.write import WriteTool
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.execution.local.sandbox import PlatformSandbox


def build_tools(
    workspace: Path,
    *,
    snapshots: SnapshotStore | None = None,
    sandbox: PlatformSandbox | None = None,
) -> tuple[WorkspaceTool, ...]:
    """构造共享执行环境、权限和快照的工作区工具。"""
    tools = (
        ReadTool(workspace),
        WriteTool(workspace, snapshots),
        EditTool(workspace, snapshots),
        BashTool(workspace, snapshots),
        PowerShellTool(workspace, snapshots),
    )
    for tool in tools:
        tool.sandbox = sandbox
    return tools
