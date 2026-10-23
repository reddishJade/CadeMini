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
from cade.coding.tools.truncate import MAX_BYTES, format_size, truncate
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.execution.local.subprocess import (
    SubprocessShell,
)
from cade.harness.execution.paths import resolve_path
from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult, current_tool_context


class BashTool(WorkspaceTool):
    name = "bash"
    description = "Execute a bash command in the current working directory. Returns stdout and stderr. Output is truncated to last 2000 lines or 50KB (whichever is hit first). If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds."
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
        if isinstance(timeout, int | float) and timeout * 1000 > 2_147_483_647:
            raise ValueError("Invalid timeout: maximum is 2147483.647 seconds")
        timeout_ms = int(timeout * 1000) if isinstance(timeout, int | float) else None
        runtime = current_tool_context()
        environment = self.sandbox.environment() if self.sandbox else dict(os.environ)
        variables = {
            "CADE_SESSION_ID": runtime.session_id,
            "CADE_SESSION_FILE": runtime.session_file,
            "CADE_PROVIDER": runtime.provider,
            "CADE_MODEL": runtime.model,
            "CADE_REASONING_LEVEL": runtime.reasoning_effort,
        }
        for key, value in variables.items():
            environment.pop(key, None)
            if value:
                environment[key] = value
        rolling = ""
        last_update = 0.0

        def progress(chunk: str) -> None:
            nonlocal rolling, last_update
            if runtime.on_update is None:
                return
            rolling = (
                (rolling + chunk)
                .encode("utf-8")[-MAX_BYTES * 2 :]
                .decode("utf-8", "ignore")
            )
            if time.monotonic() - last_update >= 0.1:
                runtime.on_update(truncate(rolling, tail=True).content)
                last_update = time.monotonic()

        if runtime.on_update is not None:
            runtime.on_update("")
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
                on_progress=progress,
            )
        elapsed = round(time.monotonic() - started, 1)
        if runtime.on_update is not None:
            runtime.on_update(truncate(rolling, tail=True).content)
        full_output = result.output
        full_path = result.full_output_path
        if full_path:
            full_output = Path(full_path).read_bytes().decode("utf-8", "replace")
        truncation = truncate(full_output, tail=True)
        if truncation.truncated and full_path is None:
            import tempfile

            with tempfile.NamedTemporaryFile(
                prefix=f"cade-{self.name}-", suffix=".log", delete=False
            ) as stream:
                stream.write(full_output.encode("utf-8"))
                full_path = stream.name
        output = truncation.content or "(no output)"
        details: dict[str, Any] = {}
        if truncation.truncated:
            first = truncation.totalLines - truncation.outputLines + 1
            last = truncation.totalLines
            if truncation.lastLinePartial:
                last_line = full_output.removesuffix("\n").rsplit("\n", 1)[-1]
                output += f"\n\n[Showing last {format_size(truncation.outputBytes)} of line {last} (line is {format_size(len(last_line.encode()))}). Full output: {full_path}]"
            else:
                note = (
                    f" ({format_size(MAX_BYTES)} limit)"
                    if truncation.truncatedBy == "bytes"
                    else ""
                )
                output += f"\n\n[Showing lines {first}-{last} of {last}{note}. Full output: {full_path}]"
            details.update(truncation=truncation.record(), fullOutputPath=full_path)
        if result.timed_out:
            output = (
                output if truncation.content else ""
            ) + f"\n\nCommand timed out after {timeout:g} seconds"
        elif result.cancelled:
            output = (output if truncation.content else "") + "\n\nCommand aborted"
        elif result.returncode is None:
            output += "\n\nCommand terminated without an exit code"
        elif result.returncode != 0:
            output += f"\n\nCommand exited with code {result.returncode}"
        details.update(exit_code=result.returncode, wall_time_seconds=elapsed)
        if result.timed_out or result.cancelled:
            raise ValueError(output.lstrip("\n"))
        return ToolResult(
            content=output.lstrip("\n")
            if result.timed_out or result.cancelled
            else output,
            is_error=(result.returncode != 0 or result.timed_out or result.cancelled),
            metadata=details,
        )

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
