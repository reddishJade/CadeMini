"""执行 shell 命令并捕获已知文件变更。"""

from __future__ import annotations

import shutil
import sys
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

from cade.coding.security.shell import mutation_targets
from cade.coding.tools.base import (
    MAX_TOOL_CHARS,
    WorkspaceTool,
    _ensure_not_blocked,
    display_path,
    int_arg,
    resolve_workspace_path,
    text_arg,
)
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.execution.local.subprocess import (
    SubprocessShell,
)
from cade.harness.execution.paths import resolve_path
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult


class BashTool(WorkspaceTool):
    name = "bash"
    description = (
        "Run a shell command in the workspace. Commands run through "
        "PowerShell on Windows and /bin/sh on POSIX. Write scope stays bound "
        "to the project and private temp, regardless of workdir. Request only "
        "necessary additional_write_paths for task-authorized external writes; "
        "never widen scope to bypass a policy or Reviewer rejection."
    )

    def __init__(
        self,
        workspace: Path,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        super().__init__(workspace)
        self.snapshots = snapshots

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 300_000},
                "workdir": {"type": "string"},
                "additional_write_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Request approval to write these existing paths for this command only. Do not widen scope to bypass a policy or Reviewer rejection.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        command = text_arg(args, "command").strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout_ms = int_arg(args, "timeout_ms", 30_000, 1, 300_000)
        workdir = resolve_path(self.workspace, args.get("workdir", "."))
        _ensure_not_blocked(self.workspace, workdir)
        if not workdir.is_dir():
            raise ValueError(
                f"workdir is not a directory: {display_path(self.workspace, workdir)}"
            )
        with (
            self.snapshots.workspace_lock()
            if self.snapshots is not None
            else nullcontext(),
            ExitStack() as changes,
        ):
            if self.snapshots is not None:
                targets: list[Path] = []
                for raw in mutation_targets(command):
                    try:
                        target = resolve_workspace_path(workdir, raw)
                    except ValueError:
                        targets.clear()
                        break
                    if target.is_dir() or not target.is_relative_to(self.workspace):
                        targets.clear()
                        break
                    targets.append(target)
                for target in targets:
                    changes.enter_context(self.snapshots.change(target))
            result = SubprocessShell(self.sandbox).run(
                _shell_argv(command),
                cwd=workdir,
                timeout=timeout_ms,
                env=self.sandbox.environment() if self.sandbox else None,
            )
        output = result.output.strip() or "(no output)"
        if result.timed_out:
            output += f"\nCommand timed out after {timeout_ms}ms"
        elif result.cancelled:
            output += "\nCommand cancelled"
        if result.returncode not in (0, None):
            output = f"exit code: {result.returncode}\n{output}"
        return ToolResult(
            content=_truncate_tail(output),
            is_error=(
                result.returncode not in (0, None)
                or result.timed_out
                or result.cancelled
            ),
            metadata={"exit_code": result.returncode},
        )


def _shell_argv(command: str) -> list[str]:
    if sys.platform.startswith("win"):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise RuntimeError("PowerShell is not available")
        return [executable, "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


def _truncate_tail(content: str) -> str:
    if len(content) <= MAX_TOOL_CHARS:
        return content
    head = content[:8_000]
    tail = content[-(MAX_TOOL_CHARS - 8_000) :]
    return f"{head}\n[...output truncated...]\n{tail}"
