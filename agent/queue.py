"""会话级串行队列：保证同一会话的处理严格串行。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuedMessage:
    text: str
    images: list[str] | None = None
    result: Any = field(default=None)


class SessionQueue:
    """每会话串行队列。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    async def enqueue(self, text: str, images: list[str] | None = None) -> Any:
        msg = QueuedMessage(text=text, images=images)
        await self._queue.put(msg)
        await self._queue.join()
        return msg.result

    async def start(
        self, loop_fn: Callable[[str, list[str] | None], Awaitable[Any]]
    ) -> None:
        self._worker = asyncio.create_task(self._process_loop(loop_fn))

    async def _process_loop(self, loop_fn) -> None:
        while True:
            msg = await self._queue.get()
            try:
                msg.result = await loop_fn(msg.text, msg.images)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
