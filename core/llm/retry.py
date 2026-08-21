"""LLM 重试工具：指数退避重试，支持回滚与请求重建

提供统一的重试逻辑，消除 ConversationService 中 asend/asend_chat/astream_send
三处重复的重试循环。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from core.llm.exceptions import LLMRequestError

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


async def async_llm_retry(
    *,
    max_retries: int = 3,
    prepare: Callable[[], Awaitable[T]],
    execute: Callable[[T], Awaitable[R]],
    on_failure: Callable[[], Awaitable[None]],
    operation_name: str = "LLM 调用",
) -> R:
    """LLM 专用指数退避重试器。

    每次重试前调用 prepare() 重建请求，失败后调用 on_failure() 回滚状态。

    Args:
        max_retries: 最大重试次数（总尝试次数 = max_retries + 1）。
        prepare: 每次重试前调用，返回请求对象。
        execute: 使用请求对象执行 LLM 调用。
        on_failure: 每次失败后的回调（用于回滚用户消息）。
        operation_name: 操作名称（用于日志）。

    Returns:
        execute 的成功返回值。

    Raises:
        LLMRequestError: 所有重试均失败时抛出最后一个错误。
    """
    last_error: LLMRequestError | None = None

    for attempt in range(max_retries):
        try:
            request = await prepare()
            return await execute(request)

        except LLMRequestError as e:
            last_error = e
            await on_failure()

            if attempt < max_retries - 1:
                delay = 1.0 * (2 ** attempt)
                logger.warning(
                    "%s 失败，准备重试 | attempt=%d/%d | delay=%.1fs | error=%s",
                    operation_name,
                    attempt + 1,
                    max_retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)

    # 所有重试均失败
    assert last_error is not None
    logger.error(
        "%s 最终失败 | attempts=%d | error=%s",
        operation_name,
        max_retries,
        last_error,
    )
    raise last_error
