"""Cade Mini 的工作区权限策略。"""

from __future__ import annotations

from pathlib import Path

from .loop import Decision
from .tools import resolve_workspace_path
from .types import ToolCallBlock


class WorkspacePolicy:
    """只读工具放行，写入与 shell 询问，越界路径拒绝。"""

    def __init__(self, workspace: Path, *, read_only: bool = False) -> None:
        self.workspace = workspace.resolve()
        self.read_only = read_only

    def check(self, call: ToolCallBlock) -> Decision:
        if call.name in {"glob", "grep", "history"}:
            return "allow"
        if call.name == "read":
            return "allow" if self._inside(call.args.get("path")) else "deny"
        if call.name in {"write", "edit"}:
            if self.read_only:
                return "deny"
            if not self._inside(call.args.get("path")):
                return "deny"
            return "ask"
        if call.name == "bash":
            if self.read_only:
                return "deny"
            workdir = call.args.get("workdir", ".")
            if not self._inside(workdir):
                return "deny"
            return "ask"
        return "deny"

    def _inside(self, raw: object) -> bool:
        try:
            resolve_workspace_path(self.workspace, raw)
        except ValueError:
            return False
        return True
