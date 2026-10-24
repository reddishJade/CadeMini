"""模式约束、显式规则、会话授权和逐调用权限。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from cade.coding.security.grants import GrantStore
from cade.coding.security.rules import RuleConfig
from cade.coding.security.sensitive import contains_sensitive_path, is_sensitive_path
from cade.coding.security.shell import (
    classify_command,
    risk_reason,
    sensitive_command_reason,
)
from cade.harness.execution.paths import resolve_path
from cade.harness.execution.scope import ExecutionScope, SandboxMode
from cade.harness.permissions import Decision, PermissionCheck
from cade.harness.session.types import ToolCallBlock
from cade.harness.tools import Tool

type Mode = Literal["plan", "build", "ask"]

_PLAN_TOOLS = frozenset(
    {
        "read",
        "write",
        "edit",
        "question",
        "websearch",
        "webfetch",
        "history",
        "skill",
        "delegate",
        "bash",
        "powershell",
    }
)


def filter_tools(
    tools: Sequence[Tool], mode: Mode, *, network: bool = True
) -> tuple[Tool, ...]:
    """按模式和网络能力筛选可见工具；执行权限仍由策略检查。"""
    return tuple(
        tool
        for tool in tools
        if (mode != "plan" or tool.name in _PLAN_TOOLS)
        and (network or tool.name not in {"websearch", "webfetch"})
    )


class WorkspacePolicy:
    """先约束，再匹配规则和授权，最后应用模式默认值。"""

    def __init__(
        self,
        workspace: Path,
        *,
        read_only: bool = False,
        mode: Mode = "build",
        sandbox: SandboxMode = "workspace-write",
        temp_dir: Path | None = None,
        plan_dir: Path | None = None,
        rules: Sequence[RuleConfig] = (),
        grants: GrantStore | None = None,
        network: bool = True,
    ) -> None:
        self.workspace = workspace.resolve()
        self.read_only = read_only or sandbox == "read-only"
        self.sandbox: SandboxMode = sandbox
        self.temp_dir = temp_dir.resolve() if temp_dir else None
        self.plan_dir = (plan_dir or self.workspace / ".cade-plans").resolve()
        self.rules = tuple(rules)
        self.network = network
        self.grants = grants or GrantStore()
        self._mode: Mode = "build"
        self.set_mode(mode)

    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in {"plan", "build", "ask"}:
            raise ValueError(f"invalid mode: {mode!r}")
        if mode == "plan":
            self._mode = "plan"
        elif mode == "ask":
            self._mode = "ask"
        else:
            self._mode = "build"

    def check(self, call: ToolCallBlock) -> PermissionCheck:
        scope = ExecutionScope(read_only=self.read_only or self.mode == "plan")
        name = call.name
        if name in {"websearch", "webfetch"} and not self.network:
            return PermissionCheck("deny", "Network access is disabled.", scope)
        path: Path | None = None
        workdir = self.workspace
        try:
            if name in {"read", "write", "edit"}:
                path = resolve_path(self.workspace, call.args.get("path", "."))
            elif name in {"bash", "powershell"}:
                workdir = resolve_path(self.workspace, call.args.get("workdir", "."))
        except ValueError as exc:
            return PermissionCheck("deny", str(exc), scope)
        if name not in _PLAN_TOOLS | {"goal", "memory"} and not name.startswith(
            "mcp__"
        ):
            return PermissionCheck("deny", f"Unknown tool: {name}", scope)
        mutating = name in {"write", "edit", "memory"} or name.startswith("mcp__")
        mutating |= name == "goal" and call.args.get("action", "get") != "get"
        if self.read_only and mutating:
            return PermissionCheck("deny", "The session is read-only.", scope)
        if self.mode == "plan" and mutating:
            if (
                name not in {"write", "edit"}
                or path is None
                or not path.is_relative_to(self.plan_dir)
            ):
                return PermissionCheck(
                    "deny",
                    "Plan mode cannot modify project files or external state.",
                    scope,
                )
            scope = ExecutionScope(read_only=True, writable_paths=(path,))
        if path is not None and self._protected(path):
            return PermissionCheck("deny", f"Protected path: {path}", scope)
        sensitive_path = path is not None and self._sensitive(path)
        if name in {"bash", "powershell"} and (self.read_only or self.mode == "plan"):
            if classify_command(_command(call)) != "read_only":
                return PermissionCheck(
                    "deny",
                    "Only read-only shell commands are available in this mode.",
                    scope,
                )
            if call.args.get("additional_write_paths"):
                return PermissionCheck(
                    "deny", "Write permissions cannot be requested in this mode.", scope
                )
        additional: tuple[Path, ...] = ()
        if name in {"bash", "powershell"}:
            raw_paths = call.args.get("additional_write_paths", [])
            if not isinstance(raw_paths, list) or any(
                not isinstance(raw, str) for raw in raw_paths
            ):
                return PermissionCheck(
                    "deny", "additional_write_paths must be a list of paths.", scope
                )
            try:
                additional = tuple(
                    resolve_path(self.workspace, raw) for raw in raw_paths
                )
            except ValueError as exc:
                return PermissionCheck("deny", str(exc), scope)
            if any(
                self._protected(target) or self._contains_protected(target)
                for target in additional
            ):
                return PermissionCheck(
                    "deny", "Requested write scope includes protected paths.", scope
                )
        elif name in {"write", "edit"} and path is not None:
            if not path.is_relative_to(self.workspace):
                additional = (path,)
        if additional:
            scope = ExecutionScope(scope.read_only, additional)
        rule = self._match_rule(call, path)
        if rule == "deny":
            return PermissionCheck(
                "deny", "An explicit permission rule denies this action.", scope
            )
        if name in {"bash", "powershell"}:
            sensitive_reason = sensitive_command_reason(_command(call), workdir)
            if sensitive_reason:
                return PermissionCheck("ask", sensitive_reason, scope)
        if sensitive_path:
            return PermissionCheck(
                "ask",
                f"Sensitive path requires approval: {path}",
                scope,
            )
        if additional and any(
            self._sensitive(target) or self._contains_sensitive(target)
            for target in additional
        ):
            return PermissionCheck(
                "ask",
                "Requested write scope includes sensitive paths.",
                scope,
            )
        plan_write = self.mode == "plan" and name in {"write", "edit"}
        if rule == "ask":
            # 会话 grant 可以回答同一 ask，但不能覆盖任何 deny。
            if not self.grants.match(call):
                return PermissionCheck(
                    "ask", "An explicit permission rule requires approval.", scope
                )
        elif rule == "allow":
            return PermissionCheck(
                "allow", "Allowed by an explicit permission rule.", scope
            )
        if self.grants.match(call):
            return PermissionCheck("allow", "Allowed by a session grant.", scope)
        if plan_write:
            return PermissionCheck(
                "ask", "Writing a plan file requires explicit user approval.", scope
            )
        if (
            additional
            and any(not self._writable(path) for path in additional)
            and self.sandbox != "danger-full-access"
        ):
            return PermissionCheck(
                "ask",
                "This action requests write access outside the default writable scope.",
                scope,
            )
        if mutating and path == self.workspace / "cade.json":
            return PermissionCheck(
                "ask", "Changing agent configuration may change security policy.", scope
            )
        if name.startswith("mcp__"):
            return PermissionCheck(
                "ask",
                "MCP tools may affect resources outside the local sandbox.",
                scope,
            )
        if name in {"bash", "powershell"}:
            reason = risk_reason(_command(call), self.workspace, workdir=workdir)
            if reason:
                return PermissionCheck("ask", reason, scope)
            if self.mode == "ask" and classify_command(_command(call)) != "read_only":
                return PermissionCheck(
                    "ask",
                    "Ask mode requires approval for non-read-only commands.",
                    scope,
                )
        elif self.mode == "ask" and mutating:
            return PermissionCheck(
                "ask", "Ask mode requires approval for changes.", scope
            )
        return PermissionCheck("allow", scope=scope)

    def _writable(self, path: Path) -> bool:
        return path.is_relative_to(self.workspace) or (
            self.temp_dir is not None and path.is_relative_to(self.temp_dir)
        )

    def _protected(self, path: Path) -> bool:
        roots = (
            self.workspace / ".git" / "hooks",
            self.workspace / ".git" / "config",
            self.workspace / ".cade",
            Path.home() / ".cade",
        )
        if self.mode == "plan" and path.is_relative_to(self.plan_dir):
            return False
        return any(
            path == root.resolve() or path.is_relative_to(root.resolve())
            for root in roots
        )

    def _sensitive(self, path: Path) -> bool:
        return is_sensitive_path(path)

    def _contains_sensitive(self, path: Path) -> bool:
        return contains_sensitive_path(path, self.workspace)

    def _contains_protected(self, path: Path) -> bool:
        return any(
            root.resolve().is_relative_to(path)
            for root in (
                self.workspace / ".git" / "hooks",
                self.workspace / ".git" / "config",
                self.workspace / ".cade",
                Path.home() / ".cade",
            )
        )

    def _match_rule(self, call: ToolCallBlock, path: Path | None) -> Decision | None:
        decisions: set[Decision] = set()
        for rule in self.rules:
            if rule.tool not in {call.name, "*"}:
                continue
            if rule.path_prefix is not None:
                if path is None:
                    continue
                prefix = resolve_path(self.workspace, rule.path_prefix)
                if path != prefix and not path.is_relative_to(prefix):
                    continue
            if rule.command_prefix is not None and not _command(call).startswith(
                rule.command_prefix
            ):
                continue
            decisions.add(rule.decision)
        return next(
            (item for item in ("deny", "ask", "allow") if item in decisions), None
        )

    def describe(self) -> str:
        """把当前模式约束放入每次请求，避免模式切换后使用旧指令。"""
        text = f"Execution mode: {self.mode}. Filesystem policy: {self.sandbox}."
        if self.mode == "plan":
            text += (
                " Explore and discuss the plan. Do not implement changes or delegate changes. "
                f"Only write a plan file under {self.plan_dir} when the user explicitly requests it. "
                "Implementation requires the user to switch out of Plan mode."
            )
        text += " Never bypass a denial; adjust the action to respect its reason."
        return text


def _command(call: ToolCallBlock) -> str:
    command = call.args.get("command")
    return command if isinstance(command, str) else ""
