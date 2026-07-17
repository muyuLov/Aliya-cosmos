"""TTS 语音播放（弹性播放 + 重试 + 生命周期日志）

提供 ``speak_text``：将文本合成为语音并播放。Agent 会在每次生成
最终回复后自动调用它，无需 LLM 主动触发工具，从而保证 TTS 稳定可用。
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from core.logger import get_logger

_logger = get_logger(__name__)

# 重试配置：仅对“尚未播放任何音频”的整次合成失败做安全重试
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 0.5  # 指数退避基础延迟（秒）


def _log_lifecycle(event: str, **fields: object) -> None:
    """记录 TTS 播放生命周期事件，关键字段嵌入消息便于追踪。"""
    parts = " | ".join(f"{k}={v}" for k, v in fields.items())
    _logger.info("TTS 生命周期 | event=%s%s", event, f" | {parts}" if parts else "")


async def speak_text(text: str, context: ToolContext) -> bool:
    """合成并播放文本，返回是否成功播放。

    内部使用弹性播放器：主通道为本地 sounddevice，失败回退到
    WebSocket 转发 / 文件落盘；对“尚未播放任何音频”的整次合成失败
    做指数退避重试。任何异常均被吞掉，避免影响主对话流程。
    """
    if not context.tts_service or not text:
        return False

    from core.tts import TTSRequest
    from core.tts.player.sink import FileAudioSink, ResilientAudioPlayer
    from core.tts.player.ws_sink import WebSocketAudioSink

    # 构建弹性播放器：主通道为本地 sounddevice，失败回退到 WebSocket / 文件
    fallbacks = []
    if context.audio_relay:
        fallbacks.append(WebSocketAudioSink(context.audio_relay))
    fallbacks.append(FileAudioSink(output_dir="data/cache/tts_fallback"))

    player = ResilientAudioPlayer(
        primary=context.audio_player,
        fallbacks=fallbacks,
        on_event=lambda ev, info: _log_lifecycle(ev, **info),
    )

    request = TTSRequest(text=text)
    last_error: Exception | None = None

    _log_lifecycle("synthesize_start", text_len=len(text), max_retries=_MAX_RETRIES)

    try:
        for attempt in range(_MAX_RETRIES + 1):
            delivered_before = player.delivered_bytes
            start_ts = time.monotonic()
            got_first = False

            async def _count(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
                nonlocal got_first
                async for chunk in chunks:
                    if not chunk:
                        continue
                    if not got_first:
                        got_first = True
                        _log_lifecycle(
                            "ttfb",
                            latency=round(time.monotonic() - start_ts, 3),
                            attempt=attempt,
                        )
                    yield chunk

            try:
                await player.play_stream(_count(context.tts_service.synthesize(request)))

                if player.delivered_bytes == 0:
                    raise RuntimeError("TTS 合成未产生任何音频数据")

                _log_lifecycle(
                    "playback_complete",
                    bytes=player.delivered_bytes,
                    channel=player.active_channel,
                )
                if context.send_message:
                    await context.send_message({
                        "type": "tts_complete",
                        "text": text,
                        "audio_size": player.delivered_bytes,
                    })
                return True

            except Exception as e:
                last_error = e
                already_played = player.delivered_bytes - delivered_before
                _log_lifecycle(
                    "playback_failed",
                    attempt=attempt,
                    error=str(e),
                    already_played=already_played,
                    channel=player.active_channel,
                )
                # 已播放部分音频则不再重试（避免重复语音）；或已达最大重试次数
                if already_played > 0 or attempt >= _MAX_RETRIES:
                    break
                _log_lifecycle("retry_attempt", attempt=attempt + 1)
                await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
    except Exception as e:
        _logger.error("TTS 播放异常（已忽略，不影响对话）: %s", e, exc_info=True)
    finally:
        try:
            await player.aclose()
        except Exception:
            pass

    _log_lifecycle("synthesize_aborted", error=str(last_error), bytes=player.delivered_bytes)
    return False
