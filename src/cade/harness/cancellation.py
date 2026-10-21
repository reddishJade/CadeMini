"""跨线程取消通知与异步等待。"""

from __future__ import annotations

import asyncio
from threading import Lock


class CancellationToken:
    """跨线程通知取消；同时唤醒等待中的 provider 请求。"""

    def __init__(self) -> None:
        self._cancelled = False
        self.reason = "cancelled"
        self._lock = Lock()
        self._waiters: set[asyncio.Future[None]] = set()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._cancelled:
                return
            self.reason = reason
            self._cancelled = True
            for waiter in self._waiters:
                waiter.get_loop().call_soon_threadsafe(self._wake, waiter)

    @staticmethod
    def _wake(waiter: asyncio.Future[None]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    async def wait(self) -> None:
        """等待取消通知，结束时移除订阅，避免遗留后台线程。"""
        waiter = asyncio.get_running_loop().create_future()
        with self._lock:
            if self._cancelled:
                return
            self._waiters.add(waiter)
        try:
            await waiter
        finally:
            with self._lock:
                self._waiters.discard(waiter)

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled
