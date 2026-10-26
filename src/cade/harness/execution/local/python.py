"""执行一次 Python 程序，桥接宿主工具并收尾进程和输出。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from io import BufferedReader, BufferedWriter
from pathlib import Path
from typing import Any, cast

from cade.harness.session.types import JsonValue
from cade.harness.tools import ToolResult

from ._process import close_pipes, kill_process
from .sandbox import PlatformSandbox

_MAX_FRAME_BYTES = 1024 * 1024
_MAX_OUTPUT_BYTES = 50 * 1024


@dataclass(frozen=True)
class PythonResult:
    output: str
    value: JsonValue = None
    error: str | None = None


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


async def run_python(
    code: str,
    *,
    workspace: Path,
    sandbox: PlatformSandbox | None,
    names: Sequence[str],
    invoke: Callable[[str, dict[str, JsonValue]], Awaitable[ToolResult]],
) -> PythonResult:
    """启动新解释器；调用方的取消和超时负责中止整次执行。"""
    argv = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(Path(__file__).with_name("_python_worker.py")),
    ]
    command = sandbox.wrap(argv, workspace) if sandbox else None
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "TEMP", "TMP", "TMPDIR"}
    }
    if sandbox:
        env.update({key: str(sandbox.temp_dir) for key in ("TMPDIR", "TEMP", "TMP")})
    try:
        # 同步创建后立即接管进程，避免取消发生在后台创建完成之前。
        process = subprocess.Popen(  # noqa: ASYNC220
            command.argv if command else argv,
            cwd=command.cwd if command else workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=sys.platform != "win32",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError:
        if command and command.finalize:
            command.finalize()
        raise
    incoming = cast(BufferedReader, process.stdout)
    outgoing = cast(BufferedWriter, process.stdin)
    errors = cast(BufferedReader, process.stderr)
    output = bytearray()

    def capture() -> bool:
        while chunk := errors.read1(8192):
            remaining = _MAX_OUTPUT_BYTES - len(output)
            output.extend(chunk[:remaining])
            if len(chunk) > remaining:
                return False
        return True

    def send(value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode() + b"\n"
        if len(raw) > _MAX_FRAME_BYTES:
            raise ValueError("Python control message exceeds 1 MiB")
        outgoing.write(raw)
        outgoing.flush()

    async def exchange() -> dict[str, Any]:
        await asyncio.to_thread(send, {"code": code, "tools": list(names)})
        while True:
            raw = await asyncio.to_thread(incoming.readline, _MAX_FRAME_BYTES + 1)
            if not raw:
                raise ValueError("Python process exited without a result")
            if len(raw) > _MAX_FRAME_BYTES:
                raise ValueError("Python control message exceeds 1 MiB")
            frame = json.loads(raw, parse_constant=_reject_constant)
            if not isinstance(frame, dict):
                raise TypeError("Invalid Python control message")
            if frame.get("type") == "done":
                if "error" in frame and not isinstance(frame["error"], str):
                    raise ValueError("Invalid Python error result")
                return frame
            name, args = frame.get("name"), frame.get("args")
            if (
                frame.get("type") != "call"
                or not isinstance(name, str)
                or name not in names
                or not isinstance(args, dict)
            ):
                raise ValueError("Invalid Python tool call")
            result = await invoke(name, args)
            reply = (
                {"error": result.content}
                if result.is_error
                else {"value": {"content": result.content, "metadata": result.metadata}}
            )
            try:
                await asyncio.to_thread(send, reply)
            except ValueError as exc:
                await asyncio.to_thread(send, {"error": str(exc)})

    conversation = asyncio.create_task(exchange())
    captured = asyncio.create_task(asyncio.to_thread(capture))
    frame: dict[str, Any] = {}
    error: str | None = None
    try:
        done, _ = await asyncio.wait(
            (conversation, captured), return_when=asyncio.FIRST_COMPLETED
        )
        if captured in done and not captured.result():
            raise ValueError("Python output exceeds 50 KiB")
        frame = await conversation
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        error = str(exc)
    finally:
        # 先结束进程，再等待阻塞的管道读写和嵌套工具退出。
        try:
            await asyncio.to_thread(kill_process, process)
        finally:
            conversation.cancel()
            await asyncio.gather(conversation, return_exceptions=True)
            try:
                complete = await asyncio.wait_for(asyncio.shield(captured), timeout=2)
                if not complete:
                    error = "Python output exceeds 50 KiB"
            except TimeoutError:
                error = "Python output pipe did not close after process termination"
            outgoing.close()
            close_pipes(process)
            if command and command.finalize:
                violation = command.finalize()
                if violation:
                    error = violation
    if frame.get("error"):
        error = frame["error"]
    value = frame.get("value")
    if (
        value is not None
        and len(json.dumps(value, ensure_ascii=False).encode()) + len(output)
        > _MAX_OUTPUT_BYTES
    ):
        error = "Python output and return value exceed 50 KiB"
        value = None
    return PythonResult(output.decode("utf-8", "replace"), value, error)
