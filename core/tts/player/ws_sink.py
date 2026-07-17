"""WebSocket 音频回退通道：将音频转发给前端客户端播放。

适用于无本地音频设备的服务器场景——由浏览器 / 桌面客户端负责解码与播放。
音频以 base64 分块经现有 ``send_message`` 通道推送，首块携带格式提示，
客户端据此选择解码方式。该通道与 ``AudioSink`` 协议一致，可无缝接入
``ResilientAudioPlayer`` 的备用通道链。
"""

from __future__ import annotations

import base64
from typing import Awaitable, Callable

from core.logger import get_logger
from core.tts.player.sink import _sniff_format

_logger = get_logger(__name__)

# send_message 回调签名：接收一条 JSON 可序列化字典
SendMessage = Callable[[dict], Awaitable[None]]


class WebSocketAudioSink:
    """
    WebSocket 音频通道：将音频字节经 ``send_message`` 转发给客户端。

    Args:
        send_message: 消息发送回调（通常来自 WebSocket 连接）。
        session_hint: 会话标识，随首块一并下发，便于客户端关联。
    """

    channel_name = "websocket"

    def __init__(self, send_message: SendMessage, session_hint: str = "") -> None:
        self._send = send_message
        self._session_hint = session_hint
        self._format: str | None = None
        self._sent = 0

    async def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._format is None:
            self._format = _sniff_format(chunk)
            await self._send({
                "type": "tts_audio_start",
                "format": self._format,
                "session": self._session_hint,
            })
        await self._send({
            "type": "tts_audio",
            "format": self._format,
            "data": base64.b64encode(chunk).decode("ascii"),
        })
        self._sent += len(chunk)

    async def drain(self) -> None:
        if self._sent:
            await self._send({"type": "tts_audio_end", "bytes": self._sent})

    async def aclose(self) -> None:
        pass


__all__ = ["WebSocketAudioSink", "SendMessage"]
