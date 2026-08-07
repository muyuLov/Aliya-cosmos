"""钩子注册表：横切能力（认知 / 情绪 / 记忆 / 通知）的接入点

三个钩子点（after_reply 预留，文本回复与 TTS 已由统一响应模块处理）：
- before_turn(text)    同步：认知准备（阻塞，结果注入上下文）
- after_tool(name, result)  同步：工具学习（顺序敏感）
- after_turn(reply)    同步：对话收尾（记忆保存、情绪推进调度）
- after_reply(reply)   异步可丢：预留扩展点

处理器可为异步函数（await 执行）或同步函数（直接执行），
以兼容 cognition 等同步钩子订阅者。同步钩子用 run() 顺序执行；
异步可丢钩子用 run_later() 以 create_task 调度并带错误回调，不阻塞主流程。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class HookPoint(str, Enum):
    BEFORE_TURN = "before_turn"
    AFTER_TOOL = "after_tool"
    AFTER_TURN = "after_turn"
    AFTER_REPLY = "after_reply"


HookHandler = Callable[..., Awaitable[None] | None]


class HookRegistry:
    """按钩子点注册与触发处理器的注册表。"""

    def __init__(self) -> None:
        self._handlers: dict[HookPoint, list[HookHandler]] = defaultdict(list)

    def register(self, point: HookPoint, handler: HookHandler) -> None:
        if handler not in self._handlers[point]:
            self._handlers[point].append(handler)

    def unregister(self, point: HookPoint, handler: HookHandler) -> None:
        try:
            self._handlers[point].remove(handler)
        except ValueError:
            pass

    async def run(self, point: HookPoint, *args: Any) -> None:
        """按注册顺序执行所有处理器，单个异常被隔离（记录并继续）。

        异步处理器被 await；同步处理器直接执行（如 cognition 的同步钩子）。
        """
        for handler in list(self._handlers.get(point, ())):
            try:
                result = handler(*args)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.warning("[Hook] %s 处理器异常（已隔离）: %s", point.value, e)

    def run_later(self, point: HookPoint, *args: Any) -> None:
        """以 fire-and-forget 方式调度异步处理器，不阻塞调用方。"""
        for handler in list(self._handlers.get(point, ())):
            result = handler(*args)
            if inspect.isawaitable(result):
                task = asyncio.create_task(result)  # type: ignore[arg-type]
                task.add_done_callback(self._log_error)

    @staticmethod
    def _log_error(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error("[Hook] 异步钩子异常: %s", task.exception())


__all__ = ["HookPoint", "HookRegistry"]
