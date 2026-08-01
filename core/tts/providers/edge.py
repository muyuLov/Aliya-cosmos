"""EdgeTTS 提供商实现（微软必应语音）

edge-tts 库通过 WebSocket 连接微软 Edge 在线 TTS 服务，免费且高质量。
支持代理、超时、边界事件等特性。
"""

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
    """将 float 速度因子转换为 edge-tts rate 百分比字符串。

    Args:
        speed: 语速倍率（0.5~2.0），1.0 为正常语速。

    Returns:
        edge-tts 速率字符串，如 '+50%'、'-30%'。

    Raises:
        ValueError: speed 超出有效范围时抛出。
    """
    if not 0.1 <= speed <= 5.0:
        raise ValueError(f"语速 {speed} 超出有效范围 (0.1~5.0)")
    delta = (speed - 1.0) * 100
    return f"{delta:+.0f}%"


class EdgeTTSProvider(TTSProvider):
    """
    EdgeTTS 提供商，通过微软必应语音 API 合成语音。

    edge-tts 是免费的高质量 TTS 服务，支持多种音色和语言。
    无需 API Key，直接使用微软 Edge 浏览器的 TTS 引擎。

    Args:
        config: 支持 voice（默认 "zh-CN-XiaoxiaoNeural"）、rate（默认 "+0%"）、
            volume（默认 "+0%"）、pitch（默认 "+0Hz"）、timeout（默认 60，连接和接收共用）、
            connect_timeout（覆盖连接超时）、receive_timeout（覆盖接收超时）、
            proxy（HTTP 代理 URL）、boundary（"SentenceBoundary" 或 "WordBoundary"）。
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

        # 边界事件粒度（默认句子级别，可改为单词级别）
        self._boundary: str = config.get("boundary", "SentenceBoundary")

        # 代理和超时配置
        self._proxy: str | None = config.get("proxy")  # None 表示直连
        base_timeout = config.get("timeout", 60)
        self._connect_timeout: int | None = config.get("connect_timeout", base_timeout)
        self._receive_timeout: int | None = config.get("receive_timeout", base_timeout)

        # 会话管理：session_id → (Communicate 实例, 取消标志)
        self._sessions: dict[str, tuple[edge_tts.Communicate, asyncio.Event]] = {}

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
        cancel_event = asyncio.Event()

        comm = edge_tts.Communicate(
            text=request.text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            boundary=self._boundary,
            proxy=self._proxy,
            connect_timeout=self._connect_timeout,
            receive_timeout=self._receive_timeout,
        )
        self._sessions[session_id] = (comm, cancel_event)
        return comm, session_id

    async def create_session(self, request: TTSRequest) -> str:
        """
        创建合成会话，返回 session_id。

        edge-tts 不需要服务端会话，此方法仅用于初始化 Communicate 实例。
        """
        try:
            _, session_id = self._build_communicate(request)
            _logger.debug(
                "EdgeTTS 会话创建 | session_id=%s | voice=%s | proxy=%s",
                session_id,
                request.avatar_id or self._voice,
                self._proxy or "(直连)",
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
        item = self._sessions.get(session_id)
        if item is None:
            raise TTSSessionError(
                session_id,
                f"会话不存在或已关闭: {session_id}",
            )
        comm, cancel_event = item

        async def _stream() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in comm.stream():
                    # 检查是否被 close_session 取消
                    if cancel_event.is_set():
                        _logger.debug(
                            "EdgeTTS 流被取消 | session_id=%s", session_id
                        )
                        break
                    if chunk.get("type") == "audio":
                        yield chunk["data"]
            except asyncio.CancelledError:
                _logger.debug(
                    "EdgeTTS 消费被取消 | session_id=%s", session_id
                )
                raise
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
        """释放会话资源（设置取消标志并移除会话记录）。"""
        item = self._sessions.pop(session_id, None)
        if item is not None:
            _, cancel_event = item
            cancel_event.set()  # 通知消费线程主动退出
            _logger.debug("EdgeTTS 会话已释放 | session_id=%s", session_id)

    async def aclose(self) -> None:
        """关闭所有会话并清理连接器。"""
        # 取消所有活跃会话
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)
        self._sessions.clear()

        _logger.debug("EdgeTTS 提供商已关闭，所有会话已清理")
