"""执行 shell 命令并捕获已知文件变更。"""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

from cade.coding.security.shell import mutation_targets
from cade.coding.tools.base import (
    WorkspaceTool,
    _ensure_not_blocked,
    display_path,
    resolve_workspace_path,
    text_arg,
)
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.execution.local.subprocess import (
    SubprocessShell,
)
from cade.harness.execution.paths import resolve_path
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult, current_tool_context


class BashTool(WorkspaceTool):
    name = "bash"
    description = "Execute a bash command in the current working directory. Returns stdout and stderr. Output is limited to the last 50KB. If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds."
    manages_timeout = True

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
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (optional, no default timeout)",
                },
                "workdir": {"type": "string"},
                "additional_write_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Request approval to write these existing paths for this command only. Do not widen scope to bypass a policy or Reviewer rejection.",
                },
            },
            "required": ["command"],
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        command = text_arg(args, "command")
        timeout = args.get("timeout")
        if timeout is not None and (
            not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Invalid timeout: must be a finite number of seconds")
        timeout_ms = int(timeout * 1000) if isinstance(timeout, int | float) else None
        runtime = current_tool_context()
        environment = self.sandbox.environment() if self.sandbox else dict(os.environ)
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
            started = time.monotonic()
            result = SubprocessShell(self.sandbox).run(
                self.shell_argv(command),
                cwd=workdir,
                timeout=timeout_ms,
                env=environment,
                on_progress=runtime.on_update,
            )
        elapsed = round(time.monotonic() - started, 1)
        output = result.output or "(no output)"
        if result.full_output_path:
            output += f"\n\n[Output truncated. Full output: {result.full_output_path}]"
        details: dict[str, Any] = {
            "exit_code": result.returncode,
            "wall_time_seconds": elapsed,
            "fullOutputPath": result.full_output_path,
        }
        if result.timed_out:
            output += f"\n\nCommand timed out after {timeout:g} seconds"
        elif result.cancelled:
            output += "\n\nCommand aborted"
        elif result.returncode is None:
            output += "\n\nCommand terminated without an exit code"
        elif result.returncode != 0:
            output += f"\n\nCommand exited with code {result.returncode}"
        if result.timed_out or result.cancelled:
            raise ValueError(output)
        return ToolResult(output, is_error=result.returncode != 0, metadata=details)

    def shell_argv(self, command: str) -> list[str]:
        executable = None
        if sys.platform == "win32":
            candidates = [
                Path(os.environ[name]) / "Git/bin/bash.exe"
                for name in ("ProgramFiles", "ProgramFiles(x86)")
                if os.environ.get(name)
            ]
            candidates.append(Path.home() / "scoop/apps/git/current/bin/bash.exe")
            for candidate in candidates:
                if candidate.is_file():
                    executable = str(candidate)
                    break
        elif Path("/bin/bash").is_file():
            executable = "/bin/bash"
        executable = (
            executable
            or shutil.which("bash")
            or (shutil.which("sh") if sys.platform != "win32" else None)
        )
        if executable is None:
            raise RuntimeError("bash is not available")
        return [executable, "-c", command]


class PowerShellTool(BashTool):
    name = "powershell"
    description = BashTool.description.replace("a bash", "a PowerShell")

    def shell_argv(self, command: str) -> list[str]:
        if sys.platform != "win32":
            raise RuntimeError("The powershell tool is only available on Windows.")
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise RuntimeError("PowerShell is not available")
        prefix = (
            "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n"
        )
        return [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            prefix + command,
        ]
