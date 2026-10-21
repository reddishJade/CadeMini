"""逐调用执行范围；不依赖平台沙箱实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
