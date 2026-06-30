"""LLM 调用重试工具模块

提供统一的指数退避重试逻辑，消除 extractor.py 和 rag_query.py 中的三处重复代码。
同时实现暂时性/永久性错误的区分，认证失败（401/403）等永久性错误立即终止重试。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

from core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# 可重试的 HTTP 状态码（白名单，不在白名单的 4xx 不重试）
_RETRYABLE_HTTP_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


def _get_http_status(exc: BaseException) -> int | None:
    """从异常对象中提取 HTTP 状态码（尝试常见属性名）"""
    for attr in ("status_code", "status", "http_status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    return None


def is_transient_error(exc: BaseException) -> bool:
    """判断异常是否为暂时性错误（可重试）

    永久性错误（不重试）：
    - HTTP 4xx（除 408/429 外），特别是 401/403 认证失败
    - 其他明确标记为不可重试的错误

    暂时性错误（重试）：
    - asyncio.TimeoutError
    - 连接错误（ConnectionError, OSError）
    - HTTP 5xx、408、429
    - 其他未明确分类的异常（保守策略：重试）
    """
    # 1. 超时 → 始终重试
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # 2. 连接类错误 → 重试
    if isinstance(exc, (ConnectionError, OSError)):
        return True

    # 3. 根据 HTTP 状态码判断
    status = _get_http_status(exc)
    if status is not None:
        # 4xx（除 408/429）→ 永久性，不重试
        if 400 <= status < 500 and status not in _RETRYABLE_HTTP_CODES:
            return False
        # 5xx / 408 / 429 → 重试
        if status >= 500 or status in _RETRYABLE_HTTP_CODES:
            return True

    # 4. 保守策略：未知异常视为可重试
    return True


async def async_retry(
    func: Callable[[], Awaitable[T]],
    max_retries: int,
    timeout: float,
    operation_name: str = "LLM 调用",
) -> T:
    """异步指数退避重试器

    Args:
        func:           要执行的异步可调用对象
        max_retries:    最大重试次数（总尝试次数 = max_retries + 1）
        timeout:        单次调用超时（秒）
        operation_name: 操作名称（用于日志）

    Returns:
        func 的成功返回值

    Raises:
        asyncio.TimeoutError: 所有重试均超时
        Exception:            func 抛出的其他异常（所有重试均失败）
    """
    sleep_seconds = 1.0
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(func(), timeout=timeout)

        except asyncio.TimeoutError:
            last_exc = asyncio.TimeoutError(
                f"{operation_name} 超时 ({timeout}s)"
            )
            logger.warning(
                "%s 超时 (尝试 %d/%d)", operation_name, attempt + 1, max_retries + 1
            )

        except Exception as e:
            last_exc = e
            # 永久性错误 → 不重试，立即抛出
            if not is_transient_error(last_exc):
                logger.warning(
                    "%s 永久性错误 (尝试 %d/%d): %s",
                    operation_name, attempt + 1, max_retries + 1, e,
                )
                raise

            logger.warning(
                "%s 失败 (尝试 %d/%d): %s",
                operation_name, attempt + 1, max_retries + 1, e,
            )

        # 还有重试机会 → 指数退避等待
        if attempt < max_retries:
            await asyncio.sleep(sleep_seconds)
            sleep_seconds = min(sleep_seconds * 2, 10.0)

    # 所有重试均失败
    assert last_exc is not None
    raise last_exc


async def async_retry_or_default(
    func: Callable[[], Awaitable[T]],
    max_retries: int,
    timeout: float,
    operation_name: str = "LLM 调用",
    default: T | None = None,
) -> T | None:
    """带默认值的异步重试器（失败时返回默认值而不抛异常）

    用于关键字提取等允许失败降级的场景。
    """
    try:
        return await async_retry(func, max_retries, timeout, operation_name)
    except Exception as e:
        logger.error("%s 全部重试失败: %s", operation_name, e)
        return default


__all__ = [
    "async_retry",
    "async_retry_or_default",
    "is_transient_error",
]
