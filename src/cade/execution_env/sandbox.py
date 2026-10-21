"""逐调用权限范围和平台沙箱；Windows 保留直接执行。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

type SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]


@dataclass(frozen=True)
class ExecutionScope:
    """额外可写路径只对当前调用生效，不修改会话默认权限。"""

    read_only: bool = False
    writable_paths: tuple[Path, ...] = ()


_SCOPE: ContextVar[ExecutionScope | None] = ContextVar(
    "cade_execution_scope", default=None
)


def current_scope() -> ExecutionScope:
    return _SCOPE.get() or ExecutionScope()


@contextmanager
def execution_scope(scope: ExecutionScope) -> Iterator[None]:
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


@dataclass(frozen=True)
class SandboxedCommand:
    argv: tuple[str, ...]
    cwd: Path
    finalize: Callable[[], str | None] | None = None


class CommandSandbox(Protocol):
    def wrap(self, argv: list[str], cwd: Path) -> SandboxedCommand: ...


class SandboxUnavailable(RuntimeError):
    """沙箱无法启动时拒绝执行，不回退到宿主权限。"""


class PlatformSandbox:
    """把命令交给 bubblewrap 或系统 Seatbelt。"""

    def __init__(
        self,
        workspace: Path,
        temp_dir: Path,
        *,
        mode: SandboxMode = "workspace-write",
        network: bool = True,
        protected_paths: Sequence[Path] = (),
        hidden_paths: Sequence[Path] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        self.temp_dir = temp_dir.resolve()
        self.mode: SandboxMode = mode
        self.network = network
        self.protected_paths = tuple(path.resolve() for path in protected_paths)
        self.hidden_paths = tuple(path.resolve() for path in hidden_paths)

    def wrap(self, argv: list[str], cwd: Path) -> SandboxedCommand:
        scope = current_scope()
        if sys.platform == "win32" or (
            self.mode == "danger-full-access" and not scope.read_only
        ):
            return SandboxedCommand(tuple(argv), cwd)
        roots: tuple[Path, ...] = ()
        if self.mode != "read-only":
            roots = scope.writable_paths
            if not scope.read_only:
                roots = (self.workspace, self.temp_dir, *roots)
        # 同一路径的多次挂载会掩盖更早的限制，因此先规范化并去重。
        roots = tuple(dict.fromkeys(path.resolve() for path in roots))
        if sys.platform == "linux":
            executable = shutil.which("bwrap")
            if executable is None:
                raise SandboxUnavailable("bubblewrap is required: install bwrap.")
            wrapped = self._bubblewrap(executable, roots, argv)
        elif sys.platform == "darwin":
            executable = shutil.which("sandbox-exec")
            if executable is None:
                raise SandboxUnavailable("Seatbelt sandbox-exec is unavailable.")
            wrapped = self._seatbelt(executable, roots, argv)
        else:
            raise SandboxUnavailable(f"Unsupported sandbox platform: {sys.platform}")
        return SandboxedCommand(tuple(wrapped), cwd)

    def _bubblewrap(
        self, executable: str, roots: tuple[Path, ...], argv: list[str]
    ) -> list[str]:
        args = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
        ]
        if not self.network:
            args.append("--unshare-net")
        for root in roots:
            # Shell 的额外写权限只接受已存在的挂载点。
            if not root.exists():
                raise SandboxUnavailable(
                    f"Additional shell write path must already exist: {root}"
                )
            args.extend(("--bind", str(root), str(root)))
        for path in self.protected_paths:
            if path.exists():
                args.extend(("--ro-bind", str(path), str(path)))
        for path in self.hidden_paths:
            if path.is_dir():
                args.extend(("--tmpfs", str(path), "--remount-ro", str(path)))
        args.extend(("--", *argv))
        return args

    def _seatbelt(
        self, executable: str, roots: tuple[Path, ...], argv: list[str]
    ) -> list[str]:
        forms = [
            "(version 1)",
            "(allow default)",
            "(deny file-write*)",
            '(allow file-write* (literal "/dev/null"))',
        ]
        if roots:
            paths = " ".join(f"(subpath {_sbpl(path)})" for path in roots)
            forms.append(f"(allow file-write* {paths})")
        for path in self.protected_paths:
            forms.append(f"(deny file-write* (subpath {_sbpl(path)}))")
        for path in self.hidden_paths:
            forms.append(f"(deny file-read* file-write* (subpath {_sbpl(path)}))")
        if not self.network:
            forms.append("(deny network*)")
        return [executable, "-p", "\n".join(forms), "--", *argv]

    def environment(self) -> dict[str, str]:
        """把临时文件和常见工具缓存放到会话私有目录。"""
        env = os.environ.copy()
        env.update(
            TMPDIR=str(self.temp_dir),
            TMP=str(self.temp_dir),
            TEMP=str(self.temp_dir),
            XDG_CACHE_HOME=str(self.temp_dir / "cache"),
            UV_CACHE_DIR=str(self.temp_dir / "uv-cache"),
            npm_config_cache=str(self.temp_dir / "npm-cache"),
            PYTHONPYCACHEPREFIX=str(self.temp_dir / "pycache"),
        )
        return env


def _sbpl(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=False)
