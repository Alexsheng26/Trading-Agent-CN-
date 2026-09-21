"""进程内 TTL 缓存。

行情类接口被智能体反复读取，没有缓存会把免费数据源打挂。
单机够用；多 worker 部署请换成 Redis（替换 `TTLCache` 实现即可，接口保持不变）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class TTLCache:
    def __init__(self, maxsize: int = 2048) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._maxsize = maxsize
        self._guard = asyncio.Lock()

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        if len(self._data) >= self._maxsize:
            self._evict()
        self._data[key] = (time.monotonic() + ttl, value)

    def _evict(self) -> None:
        """先清过期项；仍然满就丢掉最早到期的四分之一。"""
        now = time.monotonic()
        for key in [k for k, (exp, _) in self._data.items() if exp < now]:
            self._data.pop(key, None)
        if len(self._data) < self._maxsize:
            return
        victims = sorted(self._data.items(), key=lambda kv: kv[1][0])[: self._maxsize // 4]
        for key, _ in victims:
            self._data.pop(key, None)

    async def get_or_set(self, key: str, ttl: int, factory: Callable[[], Awaitable[T]]) -> T:
        """带单飞（single-flight）的读取：同一个 key 并发只会真正取一次。"""
        cached = self.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            # 等锁期间可能已被别人填好
            cached = self.get(key)
            if cached is not None:
                return cached  # type: ignore[no-any-return]
            value = await factory()
            self.set(key, value, ttl)
            return value

    def clear(self) -> None:
        self._data.clear()


cache = TTLCache()
