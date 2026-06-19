"""TTS 模块专用异常，错误码段：TTS_001 ~ TTS_099"""

from __future__ import annotations

from core.exception.base import StructuredException


class TTSError(StructuredException):
    """TTS 模块异常基类。"""


class TTSProviderNotFoundError(TTSError):
    """TTS 提供商未注册。"""

    def __init__(self, provider_name: str) -> None:
        super().__init__(
            code="TTS_001",
            message=f"TTS 提供商未找到: {provider_name}",
            details={"provider": provider_name},
        )


class TTSConnectionError(TTSError):
    """TTS 服务连接失败。"""

    def __init__(self, provider: str, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="TTS_002",
            message=f"[{provider}] 连接失败: {reason}",
            details={"provider": provider, "reason": reason},
            cause=cause,
        )


class TTSRequestError(TTSError):
    """TTS API 请求失败（创建会话、消费流等）。"""

    def __init__(self, provider: str, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="TTS_003",
            message=f"[{provider}] 请求失败: {reason}",
            details={"provider": provider, "reason": reason},
            cause=cause,
        )


class TTSSessionError(TTSError):
    """TTS 会话管理错误（会话不存在、释放失败等）。"""

    def __init__(self, session_id: str, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="TTS_004",
            message=f"会话错误 [{session_id}]: {reason}",
            details={"session_id": session_id, "reason": reason},
            cause=cause,
        )


class TTSConfigError(TTSError):
    """TTS 配置验证异常。"""

    def __init__(self, param_name: str, reason: str, cause: Exception | None = None) -> None:
        super().__init__(
            code="TTS_005",
            message=f"TTS 配置参数无效: {param_name} - {reason}",
            details={"param_name": param_name, "reason": reason},
            cause=cause,
        )
