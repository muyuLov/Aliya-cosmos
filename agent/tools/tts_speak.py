"""TTS 语音播放（弹性播放 + 重试 + 生命周期日志）

提供 ``speak_text``：将文本合成为语音并播放。Agent 会在每次生成
最终回复后自动调用它，无需 LLM 主动触发工具，从而保证 TTS 稳定可用。

口型同步：当使用本地 sounddevice 播放时，启动异步轮询任务实时读取
AudioPlayer 的音频特征（音量+频谱+过零率）并通过 WebSocket 发送 ``tts_features`` 消息，
用于驱动 Live2D 模型的口型参数。
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from core.logger import get_logger

_logger = get_logger(__name__)

# 重试配置：仅对"尚未播放任何音频"的整次合成失败做安全重试
_MAX_RETRIES = 2
_RETRY_BASE_DELAY = 0.5  # 指数退避基础延迟（秒）

# 口型同步：音量轮询间隔（秒），约 30fps（社区实践表明 30fps 足以驱动自然口型）
_LIP_SYNC_POLL_INTERVAL = 0.033


def _log_lifecycle(event: str, **fields: object) -> None:
    """记录 TTS 播放生命周期事件，关键字段嵌入消息便于追踪。"""
    parts = " | ".join(f"{k}={v}" for k, v in fields.items())
    _logger.info("TTS 生命周期 | event=%s%s", event, f" | {parts}" if parts else "")


async def _poll_volume(
    audio_player: object,
    send_message: object,
    stop_event: asyncio.Event,
) -> None:
    """轮询 AudioPlayer 的实时音频特征并发送到前端，收到 stop 信号时退出。

    发送特征数据格式：
    {"type": "tts_features", "volume": float, "centroid": float, "zcr": float}

    优化：
    - 噪声门 _NOISE_FLOOR=0.005：低于此值视为静音并归零，避免底噪引起的口型微动
    - 首发加速 _FIRST_MOVE_THRESHOLD=0.005：从 0→首次非零音量时降低变化门限，减少口型启动延迟
    - 常规变化门限 _CHANGE_THRESHOLD=0.02：确保静音↔说话转换能即时发送

    Args:
        audio_player: AudioPlayer 实例（须有 ``last_volume`` 等属性）。
        send_message: 消息发送回调（通常为 WebSocket send）。
        stop_event: 停止信号，置位后退出轮询。
    """
    _NOISE_FLOOR = 0.005
    _CHANGE_THRESHOLD = 0.02
    _FIRST_MOVE_THRESHOLD = 0.005  # 从 0 到首次非零的更低门限
    last_sent_vol: float = -1.0   # -1 确保首条必发
    last_sent_centroid: float = -1.0
    last_sent_zcr: float = -1.0

    def _get(attr: str, default: float) -> float:
        return getattr(audio_player, attr, default)

    try:
        while not stop_event.is_set():
            raw = _get('last_volume', 0.0)
            centroid = _get('last_centroid', 0.5)
            zcr = _get('last_zcr', 0.0)
            vol = raw if raw >= _NOISE_FLOOR else 0.0

            # 变化门限
            threshold = (
                _FIRST_MOVE_THRESHOLD
                if last_sent_vol <= 0 and vol > 0
                else _CHANGE_THRESHOLD
            )
            vol_changed = abs(vol - last_sent_vol) >= threshold
            centroid_changed = abs(centroid - last_sent_centroid) >= 0.05
            zcr_changed = abs(zcr - last_sent_zcr) >= 0.04

            if vol_changed or centroid_changed or zcr_changed:
                last_sent_vol = vol
                last_sent_centroid = centroid
                last_sent_zcr = zcr
                if send_message and callable(send_message):
                    try:
                        await send_message({
                            "type": "tts_features",
                            "volume": vol,
                            "centroid": centroid,
                            "zcr": zcr,
                        })
                    except Exception:
                        pass

            await asyncio.sleep(_LIP_SYNC_POLL_INTERVAL)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def speak_text(text: str, context: ToolContext) -> bool:
    """合成并播放文本，返回是否成功播放。

    内部使用弹性播放器：主通道为本地 sounddevice，失败回退到
    WebSocket 转发 / 文件落盘；对"尚未播放任何音频"的整次合成失败
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

    # 口型同步：当本地播放器可用时启动音量轮询
    volume_poll_task: asyncio.Task | None = None
    volume_stop_event = asyncio.Event()
    local_player = getattr(context, 'audio_player', None)
    if local_player is not None:
        volume_poll_task = asyncio.ensure_future(
            _poll_volume(local_player, context.send_message, volume_stop_event)
        )

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
        # 口型同步：停止轮询并发送复位信号（嘴巴闭合）
        if volume_poll_task is not None:
            volume_stop_event.set()
            volume_poll_task.cancel()
            try:
                await volume_poll_task
            except asyncio.CancelledError:
                pass
        if context.send_message:
            try:
                await context.send_message({"type": "tts_features", "volume": 0.0, "centroid": 0.5, "zcr": 0.0})
            except Exception:
                pass

    _log_lifecycle("synthesize_aborted", error=str(last_error), bytes=player.delivered_bytes)
    return False
