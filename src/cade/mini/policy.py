"""Cade Mini 的工作区权限策略：模式、规则与会话 grants。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from .config import RuleConfig
from .grants import GrantStore
from .loop import Decision, Tool
from .shell import classify_command
from .tools import resolve_workspace_path
from .types import ToolCallBlock

type Mode = Literal["plan", "build", "act"]

_PLAN_TOOLS = frozenset({"read", "glob", "grep", "history", "skill", "bash"})


def filter_tools(tools: Sequence[Tool], mode: Mode) -> tuple[Tool, ...]:
    """plan 模式隐藏写入工具；build/act 保留完整工具集。"""
    if mode == "plan":
        return tuple(tool for tool in tools if tool.name in _PLAN_TOOLS)
    return tuple(tools)


class WorkspacePolicy:
    """硬边界优先，其次静态规则与会话 grants，最后按模式给默认决议。"""

    def __init__(
        self,
        workspace: Path,
        *,
        read_only: bool = False,
        mode: Mode = "act",
        rules: Sequence[RuleConfig] = (),
        grants: GrantStore | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.read_only = read_only
        self.rules = tuple(rules)
        self.grants = grants or GrantStore()
        self._mode: Mode = "act"
        self.set_mode(mode)

    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode == "plan":
            self._mode = "plan"
        elif mode == "build":
            self._mode = "build"
        elif mode == "act":
            self._mode = "act"
        else:
            raise ValueError(f"invalid mode: {mode!r}")

    def check(self, call: ToolCallBlock) -> Decision:
        base = self._base_decision(call)
        if base == "deny":
            return "deny"
        if self.read_only and call.name in {"write", "edit", "bash", "memory"}:
            return "deny"
        rule = _match_rule(call, self.rules)
        if rule == "deny":
            return "deny"
        if rule == "allow":
            return "allow"
        if self.grants.match(call):
            return "allow"
        if rule == "ask":
            return "ask"
        return self._mode_decision(call)

    def _base_decision(self, call: ToolCallBlock) -> Decision:
        if call.name in {
            "glob",
            "grep",
            "history",
            "memory",
            "skill",
            "subagent",
        }:
            return "allow"
        if call.name == "read":
            return "allow" if self._inside(call.args.get("path")) else "deny"
        if call.name in {"write", "edit"}:
            return "allow" if self._inside(call.args.get("path")) else "deny"
        if call.name == "bash":
            return "allow" if self._inside(call.args.get("workdir", ".")) else "deny"
        return "deny"

    def _mode_decision(self, call: ToolCallBlock) -> Decision:
        if call.name in {"write", "edit"}:
            if self._mode == "plan":
                return "deny"
            if self._mode == "build":
                return "allow"
            return "ask"
        if call.name == "bash":
            kind = classify_command(_command(call))
            if self._mode == "plan":
                return "allow" if kind == "read_only" else "deny"
            if self._mode == "build":
                return "allow" if kind == "read_only" else "ask"
            return "ask"
        return "allow"

    def _inside(self, raw: object) -> bool:
        try:
            resolve_workspace_path(self.workspace, raw)
        except ValueError:
            return False
        return True


def _match_rule(
    call: ToolCallBlock,
    rules: Sequence[RuleConfig],
) -> Decision | None:
    for rule in reversed(rules):
        if rule.tool not in {call.name, "*"}:
            continue
        if rule.path_prefix is not None and not _normalized(
            call.args.get("path")
        ).startswith(_normalized(rule.path_prefix)):
            continue
        if rule.command_prefix is not None and not _command(call).startswith(
            rule.command_prefix
        ):
            continue
        return rule.decision
    return None


def _command(call: ToolCallBlock) -> str:
    command = call.args.get("command")
    return command if isinstance(command, str) else ""


def _normalized(value: object) -> str:
    return str(value).replace("\\", "/")
