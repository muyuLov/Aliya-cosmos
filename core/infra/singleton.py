"""异步安全惰性单例注册表。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class AsyncSingleton:
    """按 key 独立加锁的异步惰性单例注册表。"""

    _instances: dict[str, object] = {}
    _locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def _normalize(cls, key: str) -> str:
        # 路径归一化，避免路径变体创建多个实例
        p = Path(key)
        try:
            return str(p.resolve())
        except OSError:
            return str(p)

    @classmethod
    async def get_or_create(cls, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        key = cls._normalize(key)
        if key in cls._instances:
            return cls._instances[key]  # type: ignore[return-value]
        lock = cls._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in cls._instances:
                cls._instances[key] = await factory()
        return cls._instances[key]  # type: ignore[return-value]

    @classmethod
    def get_sync(cls, key: str) -> object | None:
        return cls._instances.get(cls._normalize(key))

    @classmethod
    def clear(cls, key: str | None = None) -> None:
        if key is None:
            cls._instances.clear()
            cls._locks.clear()
            return
        k = cls._normalize(key)
        cls._instances.pop(k, None)
        cls._locks.pop(k, None)
