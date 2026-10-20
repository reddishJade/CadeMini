"""Cade Mini 的六个工作区工具。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

from cade_mini.core.loop import CancellationToken, ToolResult
from cade_mini.core.types import JsonValue, ToolCallBlock
from cade_mini.execution_env import SubprocessShell
from cade_mini.execution_env.paths import resolve_path
from cade_mini.execution_env.sandbox import (
    ExecutionScope,
    PlatformSandbox,
    current_scope,
    execution_scope,
)
from cade_mini.execution_env.subprocess import process_cancellation
from cade_mini.extensions.snapshots import SnapshotStore
from cade_mini.safety.shell import mutation_targets

MAX_READ_BYTES = 50 * 1024
MAX_READ_LINES = 2_000
MAX_LINE_CHARS = 2_000
MAX_FILE_BYTES = 1_000_000
MAX_TOOL_CHARS = 64_000
MAX_GLOB_RESULTS = 1_000
MAX_GREP_RESULTS = 500

_BLOCKED_PARTS = frozenset({".git", ".venv", "__pycache__", "node_modules"})


class WorkspaceTool:
    """绑定工作区的工具基类；同步 I/O 在线程中执行。"""

    name: str
    description: str
    parameters: dict[str, Any]

    def __init__(
        self, workspace: Path, *, sandbox: PlatformSandbox | None = None
    ) -> None:
        self.workspace = workspace.resolve()
        self.snapshots: SnapshotStore | None = None
        self.sandbox = sandbox
        self.parameters = self._parameters()

    def _parameters(self) -> dict[str, Any]:
        raise NotImplementedError

    async def execute(
        self,
        call: ToolCallBlock,
        signal: CancellationToken | None = None,
    ) -> ToolResult:
        cancel = threading.Event()
        operation = self.run
        if self.sandbox is not None and sys.platform != "win32" and self.name != "bash":
            operation = self._run_confined
        with process_cancellation(cancel):
            task = asyncio.create_task(asyncio.to_thread(operation, call.args))
            try:
                while not task.done():
                    if signal is not None and signal.cancelled:
                        cancel.set()
                    await asyncio.wait({task}, timeout=0.05)
                return task.result()
            except asyncio.CancelledError:
                cancel.set()
                # 等进程退出再返回，避免超时后继续修改文件或提前清理临时目录。
                await asyncio.shield(task)
                raise

    def _run_confined(self, args: dict[str, JsonValue]) -> ToolResult:
        sandbox = self.sandbox
        if sandbox is None:
            raise RuntimeError("File worker requires an execution environment")
        target = None
        if self.name in {"write", "edit"}:
            target = _resolve_authorized_path(
                self.workspace, args.get("path"), writing=True
            )
            _ensure_not_blocked(self.workspace, target)
        snapshots = self.snapshots
        change = (
            snapshots.change(target)
            if snapshots is not None
            and target is not None
            and target.is_relative_to(self.workspace)
            else nullcontext()
        )
        scope = current_scope()
        payload = {
            "tool": self.name,
            "workspace": str(self.workspace),
            "args": args,
            "read_only": scope.read_only,
            "writable_paths": [str(path) for path in scope.writable_paths],
        }
        mounts: list[Path] = []
        for path in scope.writable_paths:
            parent = path
            while not parent.exists():
                parent = parent.parent
            mounts.append(parent)
        worker_scope = ExecutionScope(scope.read_only, tuple(mounts))
        descriptor, request_path = tempfile.mkstemp(
            dir=sandbox.temp_dir, suffix=".json"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            with change, execution_scope(worker_scope):
                result = SubprocessShell(sandbox).run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "cade_mini.tools._worker",
                        request_path,
                    ],
                    cwd=self.workspace,
                    env=sandbox.environment(),
                )
            if result.returncode != 0 or result.timed_out:
                return ToolResult(
                    content=f"Sandboxed file operation failed: {result.output}",
                    is_error=True,
                )
            protocol = result.output
            if result.full_output_path:
                full_output = Path(result.full_output_path)
                try:
                    protocol = full_output.read_text(encoding="utf-8")
                finally:
                    full_output.unlink(missing_ok=True)
            try:
                decoded = json.loads(protocol)
            except json.JSONDecodeError:
                return ToolResult(
                    content="Sandboxed file worker returned an invalid result.",
                    is_error=True,
                )
            return ToolResult(**decoded)
        finally:
            Path(request_path).unlink(missing_ok=True)

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        raise NotImplementedError


class ReadTool(WorkspaceTool):
    name = "read"
    description = (
        "Read a text file or list a directory. Use offset and limit to page "
        "through large files. Paths are relative to the workspace."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"))
        _ensure_not_blocked(self.workspace, path)
        offset = _int_arg(args, "offset", 1, 1, 1_000_000)
        limit = _int_arg(args, "limit", MAX_READ_LINES, 1, MAX_READ_LINES)
        if path.is_dir():
            return _read_directory(self.workspace, path, offset, limit)
        if not path.is_file():
            raise ValueError(f"file not found: {display_path(self.workspace, path)}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"file is too large: {size} bytes")
        data = path.read_bytes()
        if _looks_binary(data):
            raise ValueError(
                f"cannot read binary file: {display_path(self.workspace, path)}"
            )
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return ToolResult(
                content=f"{display_path(self.workspace, path)} (empty file)"
            )
        if offset > total:
            raise ValueError(f"offset {offset} is beyond the {total} lines")
        selected = lines[offset - 1 : offset - 1 + limit]
        output: list[str] = []
        used = 0
        last = offset - 1
        for index, line in enumerate(selected, start=offset):
            rendered = line[:MAX_LINE_CHARS]
            if len(line) > MAX_LINE_CHARS:
                rendered += "..."
            row = f"{index}: {rendered}"
            used += len(row.encode("utf-8")) + 1
            if used > MAX_READ_BYTES:
                output.append(
                    f"(output capped at {MAX_READ_BYTES // 1024} KB; "
                    f"continue with offset={last + 1})"
                )
                break
            output.append(row)
            last = index
        return ToolResult(
            content=(
                f"{display_path(self.workspace, path)} "
                f"(lines {offset}-{last} of {total}):\n" + "\n".join(output)
            ),
            metadata={"path": display_path(self.workspace, path)},
        )


class WriteTool(WorkspaceTool):
    name = "write"
    description = "Write UTF-8 text to a file, creating parent directories."

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
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"), writing=True)
        _ensure_not_blocked(self.workspace, path)
        if path.is_dir():
            raise ValueError(
                f"path is a directory: {display_path(self.workspace, path)}"
            )
        content = _text_arg(args, "content")
        data = content.encode("utf-8")
        change = (
            self.snapshots.change(path)
            if self.snapshots and path.is_relative_to(self.workspace)
            else nullcontext()
        )
        with change:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return ToolResult(
            content=(f"Wrote {len(data)} bytes to {display_path(self.workspace, path)}")
        )


class EditTool(WorkspaceTool):
    name = "edit"
    description = (
        "Replace one exact occurrence of old text with new text. The old text "
        "must appear exactly once."
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
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        path = _resolve_authorized_path(self.workspace, args.get("path"), writing=True)
        _ensure_not_blocked(self.workspace, path)
        if not path.is_file():
            raise ValueError(f"file not found: {display_path(self.workspace, path)}")
        old = _text_arg(args, "old")
        new = _text_arg(args, "new")
        if not old:
            raise ValueError("old must not be empty")
        text = path.read_bytes().decode("utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            raise ValueError("old text was not found")
        if count > 1:
            raise ValueError(f"old text appears {count} times; make it unique")
        change = (
            self.snapshots.change(path)
            if self.snapshots and path.is_relative_to(self.workspace)
            else nullcontext()
        )
        with change:
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
        return ToolResult(
            content=(
                f"Edited {display_path(self.workspace, path)} "
                f"({len(old)} -> {len(new)} characters)"
            )
        )


class GlobTool(WorkspaceTool):
    name = "glob"
    description = (
        "List files matching a glob pattern relative to the workspace. "
        "Use ** for recursive matches."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        pattern = _text_arg(args, "pattern")
        _validate_pattern(pattern)
        limit = _int_arg(args, "limit", 200, 1, MAX_GLOB_RESULTS)
        matches: list[str] = []
        for path in self.workspace.glob(pattern):
            if len(matches) >= limit:
                break
            if not path.is_file() or path.is_symlink():
                continue
            if _is_blocked(self.workspace, path):
                continue
            matches.append(display_path(self.workspace, path))
        if not matches:
            return ToolResult(content="No files matched.")
        return ToolResult(content="\n".join(sorted(matches)))


class GrepTool(WorkspaceTool):
    name = "grep"
    description = (
        "Search file contents with a regular expression. Returns path:line:text "
        "matches from text files in the workspace."
    )

    def _parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "glob": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, args: dict[str, JsonValue]) -> ToolResult:
        pattern = _text_arg(args, "pattern")
        glob_pattern = _text_arg(args, "glob", "**/*")
        _validate_pattern(glob_pattern)
        limit = _int_arg(args, "limit", 100, 1, MAX_GREP_RESULTS)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        matches: list[str] = []
        paths = (
            self.workspace.glob(glob_pattern)
            if "**" in glob_pattern
            else self.workspace.rglob(glob_pattern)
        )
        for path in paths:
            if len(matches) >= limit:
                break
            if not path.is_file() or path.is_symlink():
                continue
            if _is_blocked(self.workspace, path):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if _looks_binary(data):
                continue
            text = data.decode("utf-8", errors="replace")
            relative = display_path(self.workspace, path)
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{relative}:{number}: {line[:300]}")
                    if len(matches) >= limit:
                        break
        if not matches:
            return ToolResult(content="No matches.")
        return ToolResult(content="\n".join(matches))


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
        command = _text_arg(args, "command").strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout_ms = _int_arg(args, "timeout_ms", 30_000, 1, 300_000)
        workdir = resolve_path(self.workspace, args.get("workdir", "."))
        _ensure_not_blocked(self.workspace, workdir)
        if not workdir.is_dir():
            raise ValueError(
                f"workdir is not a directory: {display_path(self.workspace, workdir)}"
            )
        with ExitStack() as changes:
            if self.snapshots is not None:
                for raw in dict.fromkeys(mutation_targets(command)):
                    try:
                        target = resolve_workspace_path(workdir, raw)
                    except ValueError:
                        continue
                    if target.is_dir() or not target.is_relative_to(self.workspace):
                        continue
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


def _resolve_authorized_path(root: Path, raw: object, *, writing: bool = False) -> Path:
    path = resolve_path(root, raw)
    scope = current_scope()
    if writing:
        explicitly_writable = any(
            path == allowed or path.is_relative_to(allowed)
            for allowed in scope.writable_paths
        )
        if scope.read_only and not explicitly_writable:
            raise ValueError("This action is read-only")
        if not path.is_relative_to(root.resolve()) and not explicitly_writable:
            raise ValueError("Writing outside the workspace requires approval")
    return path


def resolve_workspace_path(root: Path, raw: object) -> Path:
    """把工具参数解析为工作区内的绝对路径。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path is required")
    candidate = Path(raw.strip())
    root = root.resolve()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        if ".." in candidate.parts:
            raise ValueError("parent-directory paths are not allowed")
        resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("path escapes the workspace")
    return resolved


def display_path(root: Path, path: Path) -> str:
    """把绝对路径转成相对工作区的 POSIX 路径。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_directory(
    root: Path,
    path: Path,
    offset: int,
    limit: int,
) -> ToolResult:
    entries = sorted(
        (entry.name + ("/" if entry.is_dir() else ""), entry.is_dir())
        for entry in path.iterdir()
        if not _is_blocked(root, entry)
    )
    selected = entries[offset - 1 : offset - 1 + limit]
    names = [name for name, _ in selected]
    suffix = (
        f"\n({len(entries)} entries; next offset={offset + len(selected)})"
        if offset - 1 + len(selected) < len(entries)
        else f"\n({len(entries)} entries)"
    )
    return ToolResult(
        content=(f"{display_path(root, path) or '.'}/:\n" + "\n".join(names) + suffix)
    )


def _is_blocked(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = path.resolve()
    if any(part in _BLOCKED_PARTS for part in relative.parts):
        return True
    name = path.name
    return name.startswith(".env") and name != ".env.example"


def _ensure_not_blocked(root: Path, path: Path) -> None:
    if _is_blocked(root, path):
        raise ValueError(f"path is blocked: {display_path(root, path)}")


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return True
    sample = data[:4096]
    if not sample:
        return False
    return sum(byte < 9 or 13 < byte < 32 for byte in sample) / len(sample) > 0.3


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


def _validate_pattern(pattern: str) -> None:
    if not pattern.strip():
        raise ValueError("pattern must not be empty")
    candidate = Path(pattern)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("pattern must stay inside the workspace")


def _text_arg(
    args: dict[str, JsonValue],
    key: str,
    default: str | None = None,
) -> str:
    value = args.get(key)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _int_arg(
    args: dict[str, JsonValue],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = args.get(key)
    if value is None:
        value = default
    if not isinstance(value, int | str | float):
        raise TypeError(f"{key} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{key} must be an integer") from None
    return min(max(number, minimum), maximum)
