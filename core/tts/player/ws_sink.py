"""WebSocket 音频回退通道：将音频转发给前端客户端播放。

适用于无本地音频设备的服务器场景——由浏览器 / 桌面客户端负责解码与播放。
音频以二进制帧直接发送（消除 base64 33% 膨胀），首块通过 JSON 携带格式提示；
如果未提供二进制通道则回退到 base64 编码的 JSON 消息。

该通道与 ``AudioSink`` 协议一致，可无缝接入
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
# send_bytes 回调签名：接收原始音频字节，直接写入 WebSocket 二进制帧
SendBytes = Callable[[bytes], Awaitable[None]]


class WebSocketAudioSink:
    """
    WebSocket 音频通道：将音频字节转发给客户端。

    优先使用二进制帧（零编码开销），未提供 ``send_bytes`` 时回退到
    base64 JSON 消息以保持向后兼容。

    Args:
        send_message: 控制消息发送回调（tts_audio_start / tts_audio_end）。
        send_bytes:  音频二进制帧发送回调，None 时回退到 base64 JSON。
        session_hint: 会话标识，随首块一并下发。
    """

    channel_name = "websocket"

    def __init__(
        self,
        send_message: SendMessage,
        send_bytes: SendBytes | None = None,
        session_hint: str = "",
    ) -> None:
        self._send = send_message
        self._send_bytes = send_bytes
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
        if self._send_bytes is not None:
            await self._send_bytes(chunk)
        else:
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


__all__ = ["WebSocketAudioSink", "SendMessage", "SendBytes"]
