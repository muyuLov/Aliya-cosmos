"""异常处理器：统一处理捕获到的异常，支持按类型注册处理策略"""

from __future__ import annotations

import logging
from collections.abc import Callable

from core.exception.base import StructuredException

HandlerFunc = Callable[[Exception], None]

# registry 值结构：(处理函数列表, suppress_default, propagate)
_RegistryEntry = tuple[list[HandlerFunc], bool, bool]


class ExceptionHandler:
    """
    异常处理器，负责统一分发和处理捕获到的异常。

    支持按异常类型注册自定义处理函数，匹配时遵循 MRO 顺序，
    即子类优先于父类匹配。未注册类型则走默认处理逻辑（记录日志）。

    与日志系统集成：默认处理通过 ``core.logger`` 记录结构化日志。

    Args:
        logger_name: 用于日志记录的 Logger 名称，默认为模块名。

    Examples:
        >>> handler = ExceptionHandler()
        >>> handler.register(ValueError, lambda e: print(f"值错误: {e}"))
        >>> handler.handle(ValueError("非法输入"))

        # 链式注册
        >>> (ExceptionHandler()
        ...     .register(ValueError, lambda e: ...)
        ...     .register(TypeError, lambda e: ...))

        # 装饰器形式注册
        >>> @handler.on(DatabaseError)
        ... def handle_db(exc: DatabaseError) -> None:
        ...     alert(exc.code)

        # propagate=True：子类匹配后继续执行父类处理函数
        >>> handler.register(StructuredException, lambda e: report(e.code))
        >>> handler.register(DatabaseError, lambda e: alert(e), propagate=True)
        >>> handler.handle(DatabaseError(...))  # alert + report 均执行
    """

    def __init__(self, logger_name: str = __name__) -> None:
        # 延迟导入，避免与 logger 模块产生循环依赖
        from core.logger import get_logger

        self._logger: logging.Logger = get_logger(logger_name)
        self._registry: dict[type[Exception], _RegistryEntry] = {}

    def register(
        self,
        exc_type: type[Exception],
        handler_func: HandlerFunc,
        *,
        suppress_default: bool = False,
        propagate: bool = False,
    ) -> ExceptionHandler:
        """
        注册特定异常类型的处理函数，支持链式调用。

        同一类型可注册多个处理函数，按注册顺序依次调用。

        Args:
            exc_type: 要处理的异常类型。
            handler_func: 处理函数。
            suppress_default: 为 True 时，匹配到此类型后跳过默认日志记录，默认 False。
            propagate: 为 True 时，执行完本类型处理函数后，继续沿 MRO 向父类传播，
                       执行父类注册的处理函数（责任链模式），默认 False。

        Returns:
            self，支持链式调用。
        """
        if exc_type in self._registry:
            funcs, existing_suppress, existing_propagate = self._registry[exc_type]
            funcs.append(handler_func)
            # suppress_default / propagate 只要有一次设为 True 即生效
            self._registry[exc_type] = (
                funcs,
                existing_suppress or suppress_default,
                existing_propagate or propagate,
            )
        else:
            self._registry[exc_type] = ([handler_func], suppress_default, propagate)
        return self

    def on(
        self,
        exc_type: type[Exception],
        *,
        suppress_default: bool = False,
        propagate: bool = False,
    ) -> Callable[[HandlerFunc], HandlerFunc]:
        """
        装饰器形式注册处理函数，等价于 register()。

        Args:
            exc_type: 要处理的异常类型。
            suppress_default: 为 True 时跳过默认日志记录。
            propagate: 为 True 时继续向父类传播执行处理函数。

        Returns:
            装饰器函数，原函数不变。

        Examples:
            >>> @handler.on(DatabaseError, suppress_default=True)
            ... def handle_db(exc: DatabaseError) -> None:
            ...     send_alert(exc.code)
        """

        def decorator(func: HandlerFunc) -> HandlerFunc:
            self.register(
                exc_type,
                func,
                suppress_default=suppress_default,
                propagate=propagate,
            )
            return func

        return decorator

    def unregister(self, exc_type: type[Exception]) -> None:
        """
        移除指定异常类型的所有处理函数。

        Args:
            exc_type: 要移除的异常类型。
        """
        self._registry.pop(exc_type, None)

    def clear(self) -> None:
        """清空所有已注册的处理函数，常用于测试环境重置状态。"""
        self._registry.clear()

    def handle(self, exception: Exception) -> None:
        """
        统一处理异常，按 MRO 顺序匹配注册的处理函数并执行。

        - propagate=False（默认）：匹配到第一个注册类型即停止向上查找。
        - propagate=True：执行完当前类型处理函数后，继续沿 MRO 向父类传播。
        - suppress_default=False（默认）：自定义函数执行完后继续走默认日志。
        - suppress_default=True：跳过默认日志记录。

        Args:
            exception: 待处理的异常实例。
        """
        funcs, suppress = self._resolve_handlers(type(exception))
        for func in funcs:
            try:
                func(exception)
            except (ValueError, TypeError, AttributeError, RuntimeError) as handler_err:
                # 处理函数自身出错时降级记录，避免掩盖原始异常
                self._logger.warning("异常处理函数执行失败: %s", handler_err, exc_info=True)

        if not suppress:
            self._default_handle(exception)

    def _resolve_handlers(self, exc_type: type[Exception]) -> tuple[list[HandlerFunc], bool]:
        """
        按 MRO 顺序收集处理函数列表。

        遇到 propagate=False 的注册项时停止向上查找；
        propagate=True 则继续沿 MRO 向父类传播。
        """
        collected: list[HandlerFunc] = []
        suppress = False

        for cls in exc_type.__mro__:
            if cls not in self._registry:
                continue
            funcs, s, prop = self._registry[cls]
            collected.extend(funcs)
            suppress = suppress or s
            if not prop:
                # 不传播，停止向上查找
                break

        return collected, suppress

    def _default_handle(self, exception: Exception) -> None:
        """将异常记录到日志：StructuredException 输出结构化字典，普通异常输出完整堆栈。"""
        if isinstance(exception, StructuredException):
            self._logger.error(
                "结构化异常: %s",
                exception.to_dict(),
                exc_info=bool(exception.cause),
            )
        else:
            self._logger.error("未处理异常: %s", exception, exc_info=exception)

    def __repr__(self) -> str:
        registered = ", ".join(cls.__name__ for cls in self._registry)
        return f"{type(self).__name__}(registered=[{registered}])"


_default_handler: ExceptionHandler | None = None


def get_default_handler() -> ExceptionHandler:
    """获取全局默认异常处理器，未初始化时自动创建。"""
    global _default_handler
    if _default_handler is None:
        _default_handler = ExceptionHandler()
    return _default_handler


def set_default_handler(handler: ExceptionHandler) -> None:
    """替换全局默认异常处理器。"""
    global _default_handler
    _default_handler = handler
