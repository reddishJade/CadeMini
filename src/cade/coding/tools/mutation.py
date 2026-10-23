"""串行执行同一文件的修改，操作结束后释放队列。"""

from __future__ import annotations

import os
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

_guard = Lock()
_queues: dict[str, tuple[LockType, int]] = {}


@contextmanager
def file_mutation(path: Path) -> Iterator[None]:
    key = os.path.normcase(str(path.resolve()))
    with _guard:
        lock, users = _queues.get(key, (Lock(), 0))
        _queues[key] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _guard:
            lock, users = _queues[key]
            if users == 1:
                del _queues[key]
            else:
                _queues[key] = (lock, users - 1)
