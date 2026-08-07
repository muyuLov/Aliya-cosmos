"""异常捕获上下文管理器与装饰器"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

if TYPE_CHECKING:
    import logging

    from core.exception.handler import ExceptionHandler

P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def catch_context(
    handler: ExceptionHandler | None = None,
    *,
    re_raise: bool = False,
    exc_types: tuple[type[Exception], ...] = (Exception,),
) -> Generator[None, None, None]:
    """
    异常捕获上下文管理器，适用于代码块级别的异常捕获。

    业务函数内部推荐直接使用 try/except + 模块 exceptions.py 中定义的
    结构化异常类，此上下文管理器适用于需要统一处理的代码块边界。

    Args:
        handler: 指定异常处理器实例，为 None 时使用全局默认处理器。
        re_raise: 处理后是否重新抛出异常，默认 False。
        exc_types: 要捕获的异常类型元组，默认捕获所有 Exception。

    Examples:
        >>> with catch_context(re_raise=True):
        ...     risky_operation()
        ...
        >>> with catch_context(exc_types=(ValueError,), re_raise=False):
        ...     parse_input(raw)
    """
    if not exc_types:
        raise ValueError("exc_types 不能为空元组，至少需要指定一个异常类型")
    try:
        yield
    except exc_types as exc:
        _get_handler(handler).handle(exc)
        if re_raise:
            raise


def _get_handler(handler: ExceptionHandler | None) -> ExceptionHandler:
    """返回传入的处理器，或全局默认处理器。"""
    if handler is not None:
        return handler
    # 延迟导入，避免循环依赖
    from core.exception.handler import get_default_handler

    return get_default_handler()


def service_error_handler(
    default_return: Any = None,
    logger: logging.Logger | None = None,
    error_message: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R | Any]]:
    """
    Service 层错误处理装饰器，自动捕获异常、记录日志并返回默认值。

    适用于 service 层函数，消除重复的 try/except 模式。

    Args:
        default_return: 异常发生时的默认返回值，默认为 None。
        logger: Logger 实例，用于记录错误日志。
        error_message: 错误消息模板，支持 {error} 占位符。

    Returns:
        装饰后的函数，异常时返回 default_return。

    Examples:
        >>> @service_error_handler(default_return=[], logger=logger)
        ... def get_items():
        ...     return database.query()
        ...
        >>> @service_error_handler(
        ...     default_return={"status": "unavailable"},
        ...     logger=logger,
        ...     error_message="获取服务状态失败：{error}",
        ... )
        ... def get_service_status():
        ...     return check_service()
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R | Any]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if logger is not None:
                    msg = error_message or f"{func.__name__} 执行失败：{e}"
                    logger.error(msg)
                return default_return

        return wrapper

    return decorator


def async_service_error_handler(
    default_return: Any = None,
    logger: logging.Logger | None = None,
    error_message: str | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R | Any]]]:
    """
    异步版本的 service 错误处理装饰器。

    Args:
        default_return: 异常发生时的默认返回值，默认为 None。
        logger: Logger 实例，用于记录错误日志。
        error_message: 错误消息模板，支持 {error} 占位符。

    Returns:
        装饰器函数，应用于异步函数。

    Examples:
        >>> @async_service_error_handler(
        ...     default_return={"answer": None, "quintuples": []},
        ...     logger=logger,
        ...     error_message="记忆查询失败：{error}",
        ... )
        ... async def query_memory(question: str):
        ...     return await memory.query(question)
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R | Any]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if logger is not None:
                    msg = error_message or f"{func.__name__} 执行失败：{e}"
                    logger.error(msg)
                return default_return

        return wrapper

    return decorator


__all__ = [
    "catch_context",
    "service_error_handler",
    "async_service_error_handler",
]
