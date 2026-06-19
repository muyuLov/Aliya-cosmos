"""异常模块公共接口"""

from core.exception.base import StructuredException
from core.exception.decorators import catch_context
from core.exception.handler import (
    ExceptionHandler,
    HandlerFunc,
    get_default_handler,
    set_default_handler,
)

__all__ = [
    "StructuredException",
    "ExceptionHandler",
    "HandlerFunc",
    "catch_context",
    "get_default_handler",
    "set_default_handler",
]
