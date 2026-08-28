"""全局写队列（WriteQueue）

串行化所有剧本写入操作，支持退避重试（瞬态错误最多 7 次）。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# 退避参数
_MAX_RETRIES = 7
_BASE_DELAY_MS = 100
_MAX_DELAY_MS = 5000


class WriteQueue:
    """全局写队列：串行执行写入任务，瞬态错误自动退避重试。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def submit(
        self, fn: Callable[[], Awaitable[Any]]
    ) -> Any:
        """提交一个写入任务，串行执行并带退避重试。

        Args:
            fn: 无参数的异步写入函数。

        Returns:
            fn 的返回值，或最终失败时返回 None。
        """
        async with self._lock:
            return await self._execute_with_retry(fn)

    async def drain(self) -> None:
        """等待所有已提交任务完成（因为串行锁，调用时通常已完成）。"""
        # submit 已在 lock 内同步执行，drain 是空操作
        pass

    async def _execute_with_retry(
        self, fn: Callable[[], Awaitable[Any]]
    ) -> Any:
        """带指数退避的重试执行。"""
        last_error: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES):
            try:
                return await fn()
            except ConnectionError as exc:
                last_error = exc
                delay_ms = min(
                    _BASE_DELAY_MS * (2 ** attempt) + random.randint(0, 50),
                    _MAX_DELAY_MS,
                )
                logger.warning(
                    "写队列瞬态失败 (attempt %d/%d): %s, 等待 %dms",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    delay_ms,
                )
                await asyncio.sleep(delay_ms / 1000)
            except OSError as exc:
                # OSError 是 ConnectionError 的父类，但更宽泛
                last_error = exc
                delay_ms = min(
                    _BASE_DELAY_MS * (2 ** attempt) + random.randint(0, 50),
                    _MAX_DELAY_MS,
                )
                logger.warning(
                    "写队列 IO 失败 (attempt %d/%d): %s, 等待 %dms",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    delay_ms,
                )
                await asyncio.sleep(delay_ms / 1000)

        logger.error(
            "写队列最终放弃 (已重试 %d 次): %s", _MAX_RETRIES, last_error
        )
        return None
