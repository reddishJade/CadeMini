"""轮次生命周期契约；实现由应用装配时注入。"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol

from cade.harness.session.log import SessionLog
from cade.harness.session.types import (
    LogEntry,
)


class TurnLifecycle(Protocol):
    """调用方提供轮次检查点；harness 不依赖工作区存储。"""

    def record(self, log: SessionLog) -> AbstractContextManager[None]: ...

    def started(self, log: SessionLog, user: LogEntry) -> None: ...

    def request_context(self, log: SessionLog) -> Sequence[str]: ...
