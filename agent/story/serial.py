"""故事级串行队列（Story Serial Queue）

按 story_id 串行化任务，不同故事可并行。
失败任务不阻塞后续任务（失败的 promise 被吞掉）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# 每个 story_id 维护一个串行 promise 链
_queues: dict[str, asyncio.Task[None]] = {}
_lock = asyncio.Lock()


async def serial(story_id: str, task: Callable[[], Awaitable[T]]) -> T | None:
    """串行执行：同一 story_id 的任务严格串行，不同故事可并行。

    - 旧任务失败不阻塞后续（.catch 被吞掉）。
    - 返回 task 的结果，或失败时返回 None。
    """
    async with _lock:
        old_task = _queues.get(story_id)

    # 如果旧任务还在，等它完成（但我们不等太久——join 本身也不阻塞）
    if old_task is not None and not old_task.done():
        try:
            await asyncio.shield(asyncio.shield(old_task))
        except (asyncio.CancelledError, Exception):
            pass  # 旧任务失败/取消不影响新任务

    result_holder: list[Any] = [None]
    error_holder: list[Exception | None] = [None]

    async def _wrapped() -> None:
        try:
            result_holder[0] = await task()
        except Exception as exc:
            error_holder[0] = exc

    new_task = asyncio.create_task(_wrapped())

    async with _lock:
        _queues[story_id] = new_task

    try:
        await new_task
    except asyncio.CancelledError:
        pass

    if error_holder[0] is not None:
        logger.warning(
            "故事级串行任务失败 (story=%s): %s", story_id, error_holder[0]
        )
        return None

    return result_holder[0]
