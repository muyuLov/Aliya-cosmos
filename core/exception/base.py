"""结构化异常基类：携带错误码、详情、异常链等上下文信息"""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any


class StructuredException(Exception):
    """
    结构化异常基类，所有业务异常应继承此类。

    相比标准 Exception，额外携带：
    - 错误码（code）：便于程序化识别与监控上报
    - 附加详情（details）：记录触发异常时的上下文参数
    - 原始异常（cause）：保留异常链，便于排查根因
    - 创建时间（timestamp）：记录异常发生的精确时间

    Args:
        code: 错误码，如 ``"E001"`` 或 ``"DB_001"``。
        message: 人类可读的错误描述。
        details: 附加上下文信息字典，默认为空字典。
        cause: 原始异常，用于构建异常链。

    Examples:
        >>> raise StructuredException("E001", "操作失败", {"user_id": 42})
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.cause = cause
        # 记录异常实例化时间，便于日志分析与监控
        self.timestamp: datetime = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """
        将异常序列化为字典，便于 JSON 输出或日志记录。

        cause 为 StructuredException 时递归序列化，否则仅保留类型与消息。

        Returns:
            包含 code、message、details、timestamp、traceback、cause 的字典。
        """
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }

        if self.__traceback__ is not None:
            data["traceback"] = "".join(traceback.format_tb(self.__traceback__))

        # 非 StructuredException 的 cause 只取类型和消息，截断递归
        if self.cause is not None:
            if isinstance(self.cause, StructuredException):
                data["cause"] = self.cause.to_dict()
            else:
                data["cause"] = {
                    "type": type(self.cause).__name__,
                    "message": str(self.cause),
                }

        return data

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"code={self.code!r}, "
            f"message={self.message!r}, "
            f"details={self.details!r})"
        )

    def with_details(self, **kwargs: Any) -> StructuredException:
        """
        链式追加 details 字段，返回自身，便于在 raise 时补充上下文。

        Args:
            **kwargs: 要追加到 details 的键值对。

        Returns:
            self，支持链式调用。

        Examples:
            >>> raise DatabaseError().with_details(user_id=42, table="orders")
        """
        self.details.update(kwargs)
        return self
