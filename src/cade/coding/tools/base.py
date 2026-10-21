"""工作区工具基类、路径限制和参数解析。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.cancellation import CancellationToken
from cade.harness.execution.local.sandbox import PlatformSandbox
from cade.harness.execution.local.subprocess import (
    SubprocessShell,
    process_cancellation,
)
from cade.harness.execution.paths import resolve_path
from cade.harness.execution.scope import ExecutionScope, current_scope, execution_scope
from cade.harness.session.types import JsonValue, ToolCallBlock
from cade.harness.tools import ToolResult

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
            _ensure_not_blocked(self.workspace, target, allow_sensitive=True)
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
                        "cade.coding.tools._worker",
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


def _is_blocked(root: Path, path: Path, *, allow_sensitive: bool = False) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = path.resolve()
    if any(part in _BLOCKED_PARTS for part in relative.parts):
        return True
    name = path.name
    return not allow_sensitive and name.startswith(".env") and name != ".env.example"


def _ensure_not_blocked(
    root: Path, path: Path, *, allow_sensitive: bool = False
) -> None:
    if _is_blocked(root, path, allow_sensitive=allow_sensitive):
        raise ValueError(f"path is blocked: {display_path(root, path)}")


def text_arg(
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


def int_arg(
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


def looks_binary(data: bytes) -> bool:
    """根据前 4 KB 中的空字节和控制字符判断二进制内容。"""
    if b"\x00" in data[:4096]:
        return True
    sample = data[:4096]
    if not sample:
        return False
    return sum(byte < 9 or 13 < byte < 32 for byte in sample) / len(sample) > 0.3
