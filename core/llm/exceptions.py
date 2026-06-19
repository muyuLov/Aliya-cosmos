"""LLM 模块专用异常，错误码段：LLM_001 ~ LLM_099"""

from __future__ import annotations

from core.exception.base import StructuredException


class LLMError(StructuredException):
    """LLM 模块异常基类，所有 LLM 相关异常均继承此类。"""


class ProviderNotFoundError(LLMError):
    """
    请求的提供商未注册。

    通常由 ProviderFactory.create() 在名称不存在时抛出。

    Args:
        provider_name: 未找到的提供商名称。
    """

    def __init__(self, provider_name: str) -> None:
        super().__init__(
            code="LLM_001",
            message=f"提供商未找到: {provider_name}",
            details={"provider": provider_name},
        )


class LLMRequestError(LLMError):
    """
    LLM API 请求失败。

    封装底层 SDK 抛出的网络错误、超时、鉴权失败等异常。

    Args:
        provider: 发生错误的提供商名称。
        reason: 错误原因描述。
        cause: 原始异常，用于保留完整异常链。
    """

    def __init__(self, provider: str, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="LLM_002",
            message=f"[{provider}] 请求失败: {reason}",
            details={"provider": provider, "reason": reason},
            cause=cause,
        )


class ContextCacheError(LLMError):
    """
    上下文缓存操作失败。

    Args:
        reason: 失败原因描述。
        cause: 原始异常，用于保留完整异常链。
    """

    def __init__(self, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="LLM_003",
            message=f"上下文缓存错误: {reason}",
            details={"reason": reason},
            cause=cause,
        )
