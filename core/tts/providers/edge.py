"""EdgeTTS 提供商实现（微软必应语音）"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncGenerator

import edge_tts

from core.logger import get_logger
from core.tts.exceptions import TTSRequestError, TTSSessionError
from core.tts.models import TTSRequest
from core.tts.providers.base import TTSProvider

_logger = get_logger(__name__)


# 速率格式转换：float → edge-tts rate 字符串
def _speed_to_rate(speed: float) -> str:
    """将 float 速度因子转换为 edge-tts rate 百分比字符串。"""
    delta = (speed - 1.0) * 100
    return f"{delta:+.0f}%"


class EdgeTTSProvider(TTSProvider):
    """
    EdgeTTS 提供商，通过微软必应语音 API 合成语音。

    edge-tts 是免费的高质量 TTS 服务，支持多种音色和语言。
    无需 API Key，直接使用微软 Edge 浏览器的 TTS 引擎。

    Args:
        config: 支持 voice（默认 "zh-CN-XiaoxiaoNeural"）、rate（默认 "+0%"）、
            volume（默认 "+0%"）、pitch（默认 "+0Hz"）、timeout（默认 60）。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)

        # 参数验证（集中校验）
        from core.tts.validation import validate_provider_config

        validate_provider_config("edge", config)

        # 音色名称（支持语言前缀，如 "zh-CN-XiaoxiaoNeural"）
        self._voice: str = config.get("voice", "zh-CN-XiaoxiaoNeural")

        # edge-tts 速率/音量/音高格式（字符串，如 "+0%", "+0Hz"）
        self._default_rate: str = config.get("rate", "+0%")
        self._default_volume: str = config.get("volume", "+0%")
        self._default_pitch: str = config.get("pitch", "+0Hz")

        # 会话管理：session_id → Communicate 实例
        self._sessions: dict[str, edge_tts.Communicate] = {}

    @property
    def provider_name(self) -> str:
        return "edge"

    def _build_communicate(
        self, request: TTSRequest
    ) -> tuple[edge_tts.Communicate, str]:
        """
        根据请求构建 edge_tts.Communicate 实例。

        Returns:
            (Communicate 实例, session_id)
        """
        voice = request.avatar_id or self._voice

        # 速率：优先使用请求中的 speed 参数（float）
        rate = self._default_rate
        if request.speed is not None:
            rate = _speed_to_rate(request.speed)

        volume = self._default_volume
        pitch = self._default_pitch

        session_id = str(uuid.uuid4())
        comm = edge_tts.Communicate(
            text=request.text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        self._sessions[session_id] = comm
        return comm, session_id

    async def create_session(self, request: TTSRequest) -> str:
        """
        创建合成会话，返回 session_id。

        实际上 edge-tts 不需要服务端会话，此方法仅用于初始化 Communicate 实例。
        """
        try:
            _, session_id = self._build_communicate(request)
            _logger.debug(
                "EdgeTTS 会话创建 | session_id=%s | voice=%s",
                session_id,
                request.avatar_id or self._voice,
            )
            return session_id
        except Exception as e:
            raise TTSRequestError(
                self.provider_name,
                f"创建会话失败: {e}",
                cause=e,
            ) from e

    def consume_session(self, session_id: str) -> AsyncGenerator[bytes, None]:
        """
        流式消费音频数据。

        Yields:
            MP3 音频块字节数据。
        """
        comm = self._sessions.get(session_id)
        if comm is None:
            raise TTSSessionError(
                session_id,
                f"会话不存在或已关闭: {session_id}",
            )

        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in comm.stream():
                    if chunk.get("type") == "audio":
                        yield chunk["data"]
            except Exception as e:
                _logger.error(
                    "EdgeTTS 流式消费异常 | session_id=%s | error=%s",
                    session_id,
                    e,
                    exc_info=True,
                )
                raise TTSSessionError(
                    session_id,
                    f"消费音频流异常: {e}",
                    cause=e,
                ) from e

        return _stream()

    async def close_session(self, session_id: str) -> None:
        """释放会话资源（从会话表中移除）。"""
        comm = self._sessions.pop(session_id, None)
        if comm is not None:
            _logger.debug("EdgeTTS 会话已释放 | session_id=%s", session_id)

    async def aclose(self) -> None:
        """关闭所有会话并清理资源。"""
        self._sessions.clear()
        _logger.debug("EdgeTTS 提供商已关闭，所有会话已清理")
