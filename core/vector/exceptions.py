# -*- coding: utf-8 -*-
"""
Vector 向量模块异常定义

使用项目的 StructuredException 基类。

错误码分配：VEC_xxx (Vector System)
- VEC_001~099: 基础错误
- VEC_100~199: Embedding 向量化错误
- VEC_200~299: 存储与检索错误
"""

from core.exception.base import StructuredException


# ============ 基础异常 (VEC_001~099) ============


class VectorError(StructuredException):
    """向量模块基础异常"""

    def __init__(
        self,
        code: str = "VEC_000",
        message: str = "",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            details=details,
            cause=cause,
        )


class VectorConfigError(VectorError):
    """向量模块配置错误"""

    def __init__(
        self,
        message: str,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="VEC_001",
            message=f"配置错误: {message}",
            details=details,
            cause=cause,
        )


class VectorNotEnabledError(VectorError):
    """向量模块未启用"""

    def __init__(
        self,
        message: str = "向量模块未启用",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="VEC_002",
            message=message,
            details=details,
            cause=cause,
        )


# ============ Embedding 向量化异常 (VEC_100~199) ============


class EmbeddingError(VectorError):
    """向量化基础异常

    ``provider`` 非空时写入 details，便于定位来源。
    """

    def __init__(
        self,
        message: str,
        code: str = "VEC_100",
        provider: str | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        if provider:
            details["provider"] = provider
        super().__init__(
            code=code,
            message=message,
            details=details,
            cause=cause,
        )


class EmbeddingAPIError(EmbeddingError):
    """调用外部 Embedding API 失败"""

    def __init__(
        self,
        message: str,
        provider: str | None = None,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code="VEC_101",
            message=f"Embedding API 调用失败: {message}",
            provider=provider,
            details=details,
            cause=cause,
        )


class DimensionMismatchError(EmbeddingError):
    """向量维度不一致"""

    def __init__(
        self,
        expected: int,
        actual: int,
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        details = details or {}
        details["expected"] = expected
        details["actual"] = actual
        super().__init__(
            code="VEC_102",
            message=f"向量维度不一致 (期望 {expected}，实际 {actual})",
            details=details,
            cause=cause,
        )


# ============ 存储与检索异常 (VEC_200~299) ============


class StoreError(VectorError):
    """向量存储基础异常（如 ID 冲突、空白文本）"""

    def __init__(
        self,
        message: str,
        code: str = "VEC_200",
        details: dict | None = None,
        cause: Exception | None = None,
    ):
        super().__init__(
            code=code,
            message=message,
            details=details,
            cause=cause,
        )


__all__ = [
    "VectorError",
    "VectorConfigError",
    "VectorNotEnabledError",
    "EmbeddingError",
    "EmbeddingAPIError",
    "DimensionMismatchError",
    "StoreError",
]
