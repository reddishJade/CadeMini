"""组装内置工作区工具。"""

from __future__ import annotations

from pathlib import Path

from cade.coding.tools.base import (
    WorkspaceTool,
)
from cade.coding.tools.bash import BashTool
from cade.coding.tools.edit import EditTool
from cade.coding.tools.read import ReadTool
from cade.coding.tools.search import GlobTool, GrepTool
from cade.coding.tools.write import WriteTool
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.execution.local.sandbox import PlatformSandbox


def build_tools(
    workspace: Path,
    *,
    snapshots: SnapshotStore | None = None,
    sandbox: PlatformSandbox | None = None,
) -> tuple[WorkspaceTool, ...]:
    """构造共享同一执行环境的六个工具。"""
    tools = (
        ReadTool(workspace),
        WriteTool(workspace, snapshots),
        EditTool(workspace, snapshots),
        GlobTool(workspace),
        GrepTool(workspace),
        BashTool(workspace, snapshots),
    )
    for tool in tools:
        tool.sandbox = sandbox
    return tools
