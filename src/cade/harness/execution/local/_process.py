from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.1
TERMINATE_GRACE_SECONDS = 3


def start_process(
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    if sys.platform == "win32":
        return subprocess.Popen(
            argv,
            shell=False,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    return subprocess.Popen(
        argv,
        shell=False,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )


def kill_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            _taskkill(proc)
        proc.wait(timeout=TERMINATE_GRACE_SECONDS)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            _taskkill(proc)
        proc.wait()


def _taskkill(proc: subprocess.Popen) -> None:
    result = subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0 and proc.poll() is None:
        diagnostic = result.stderr.strip() or result.stdout.strip()
        raise OSError(
            f"Windows process-tree termination failed (taskkill exit "
            f"{result.returncode}): {diagnostic or 'no diagnostic output'}. "
            "The execution environment must allow terminating child processes."
        )


def close_pipes(proc: subprocess.Popen) -> None:
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                logger.debug("failed to close process pipe", exc_info=True)
