from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress


class DynamicConcurrencyLimiter:
    """A FIFO concurrency limiter whose capacity can change at runtime."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("concurrency limit must be at least 1")
        self._limit = limit
        self._active = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return sum(not waiter.done() for waiter in self._waiters)

    def resize(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("concurrency limit must be at least 1")
        self._limit = limit
        self._grant_available()

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        self._waiters.append(waiter)
        self._grant_available()
        try:
            await waiter
        except BaseException:
            if waiter.done() and not waiter.cancelled():
                self.release()
            else:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
                self._grant_available()
            raise

    def release(self) -> None:
        if self._active < 1:
            raise RuntimeError("concurrency limiter released without an active permit")
        self._active -= 1
        self._grant_available()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()

    def _grant_available(self) -> None:
        while self._active < self._limit and self._waiters:
            waiter = self._waiters.popleft()
            if waiter.cancelled():
                continue
            self._active += 1
            waiter.set_result(None)
