"""Terminal lifecycle safeguards, isolated from the TUI application."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _windows_input_mode() -> tuple[Any, int, int] | None:
    """Capture Win32 console input mode, or None under ConPTY/redirected input."""
    if sys.platform != "win32":
        return None
    kernel = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if kernel is None:
        return None
    try:
        kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        kernel.GetConsoleMode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel.GetConsoleMode.restype = ctypes.c_int
        kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.SetConsoleMode.restype = ctypes.c_int
        handle = kernel.GetStdHandle(-10)
        if handle is None:
            return None
        mode = ctypes.c_ulong()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return kernel, int(handle), int(mode.value)
    except (OSError, AttributeError, ValueError, TypeError):
        return None
    return None


@contextmanager
def preserve_console_input_mode() -> Iterator[None]:
    """Restore the original Windows terminal input mode after exit or failure.

    prompt_toolkit manages raw mode for its own lifetime. The outer guard only
    restores the previously observed console flags; it does not globally patch
    prompt_toolkit or swallow application exceptions.
    """
    captured = _windows_input_mode()
    try:
        yield
    finally:
        if captured is not None:
            kernel, handle, mode = captured
            try:
                kernel.SetConsoleMode(handle, mode)
            except (OSError, AttributeError, ValueError, TypeError):
                pass
