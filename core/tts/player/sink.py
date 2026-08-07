"""音频输出通道抽象与弹性播放器。

提供三类能力，用于在不同运行环境下保证 TTS 音频至少被记录或转发出去：

- ``AudioSink`` 协议：统一 ``feed`` / ``drain`` / ``aclose`` 接口，
  ``AudioPlayer``（主通道）与各类备用通道均遵循该协议。
- ``FileAudioSink``：将音频字节原样写入文件，无需任何音频硬件，
  作为无设备环境（headless 服务器 / 容器）的兜底通道。
- ``ResilientAudioPlayer``：包裹主播放器（sounddevice），自动探测可用性，
  在设备缺失或播放中途中断时切换到备用通道，并记录播放生命周期状态。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncIterator, Callable, Protocol

from core.logger import get_logger

_logger = get_logger(__name__)


class AudioSink(Protocol):
    """音频输出通道协议：所有播放/备用通道均实现此接口。"""

    async def feed(self, chunk: bytes) -> None:
        """送入一个音频块。"""
        ...

    async def drain(self) -> None:
        """等待已送入的音频播放/写入完成。"""
        ...

    async def aclose(self) -> None:
        """释放通道资源。"""
        ...


# 生命周期事件回调： (event: str, info: dict) -> None
LifecycleCallback = Callable[[str, dict[str, object]], None]

# MP3 同步字（0xFF 后跟 0xE?/0xF?），用于格式嗅探
_MP3_SYNC = (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"\xff\xf9", b"\xff\xf8")


def _sniff_format(head: bytes) -> str:
    """根据首字节嗅探音频格式，返回 'wav' / 'mp3' / 'pcm'。"""
    if head[:4] == b"RIFF":
        return "wav"
    if head[:2] in _MP3_SYNC:
        return "mp3"
    return "pcm"


# 兜底音频文件保留上限，超出删除最旧者，避免长期运行无限累积磁盘
_MAX_FALLBACK_FILES = 100
# 兜底音频文件 TTL（秒），超过此年龄直接删除，双保险防长期静默累积
_FALLBACK_TTL_SECONDS = 48 * 3600


def _prune_fallback_dir(output_dir: Path, prefix: str, keep: int, ttl: int) -> None:
    """清理兜底音频目录：删除超过 TTL 的旧文件，并仅保留最近 ``keep`` 个文件。"""
    try:
        files = [p for p in output_dir.glob(f"{prefix}_*.*") if p.is_file()]
    except OSError:
        return
    if not files:
        return
    now = time.time()
    # TTL：删除超过年龄的文件
    for f in files:
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if age > ttl:
            try:
                f.unlink()
            except OSError:
                pass
    # 数量上限：按 mtime 保留最近 keep 个
    try:
        files = [p for p in output_dir.glob(f"{prefix}_*.*") if p.is_file()]
    except OSError:
        return
    if len(files) <= keep:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for old in files[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


class FileAudioSink:
    """
    文件音频通道：将音频字节原样写入文件，无需任何音频硬件。

    用于无音频设备（headless 服务器 / 容器）或主播放器不可用时的回退。
    首块到达时根据魔数嗅探格式，选择扩展名（.wav / .mp3 / .pcm）。
    """

    channel_name = "file"

    def __init__(self, output_dir: str | Path, prefix: str = "tts_fallback") -> None:
        self._output_dir = Path(output_dir)
        self._prefix = prefix
        self._path: Path | None = None
        self._file = None
        self._total_bytes = 0

    @property
    def output_path(self) -> Path | None:
        return self._path

    async def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        if self._file is None:
            ext = _sniff_format(chunk)
            self._output_dir.mkdir(parents=True, exist_ok=True)
            _prune_fallback_dir(
                self._output_dir, self._prefix, _MAX_FALLBACK_FILES, _FALLBACK_TTL_SECONDS
            )
            self._path = self._output_dir / f"{self._prefix}_{int(time.time() * 1000)}.{ext}"
            self._file = self._path.open("wb")
            _logger.info("文件音频通道已开启 | path=%s", self._path)
        self._file.write(chunk)
        self._total_bytes += len(chunk)

    async def drain(self) -> None:
        if self._file is not None:
            _logger.info(
                "文件音频通道写入完成 | path=%s | bytes=%d",
                self._path,
                self._total_bytes,
            )

    async def aclose(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None


class ResilientAudioPlayer:
    """
    弹性播放器：包裹主播放通道，自动回退到备用通道。

    主通道为 sounddevice 的 ``AudioPlayer``；当主通道因音频设备缺失或
    播放中途中断不可用时，自动切换到备用通道（文件 / WebSocket），
    保证 TTS 音频至少被记录或转发出去，避免 Agent 完全静默。

    Args:
        primary: 主播放器（AudioPlayer 或 None）。为 None 时直接使用备用通道。
        fallbacks: 备用通道列表，按优先级依次尝试。
        on_event: 生命周期事件回调（用于状态日志），可选。
    """

    def __init__(
        self,
        primary: AudioSink | None,
        fallbacks: list[AudioSink] | None = None,
        on_event: LifecycleCallback | None = None,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks or []
        self._on_event = on_event
        # 通道候选顺序：主通道优先，其后依次为各备用通道；用游标递进回退
        self._candidates: list[AudioSink] = []
        if primary is not None:
            self._candidates.append(primary)
        self._candidates.extend(self._fallbacks)
        self._candidate_idx = -1
        self._delivered = 0
        self._active: AudioSink | None = None
        if primary is not None:
            self._active = primary
            self._candidate_idx = 0
            self._channel = getattr(primary, "channel_name", "sounddevice")
        else:
            self._channel = "none"
            self._pick_next(reason="no_primary")

    # ------------------------------------------------------------------ #
    # 公共属性
    # ------------------------------------------------------------------ #
    @property
    def active_channel(self) -> str:
        """当前实际使用的音频通道名称。"""
        return self._channel

    @property
    def delivered_bytes(self) -> int:
        """已成功送入音频通道的累计字节数。"""
        return self._delivered

    # ------------------------------------------------------------------ #
    # 内部方法
    # ------------------------------------------------------------------ #
    def _pick_next(self, reason: str) -> bool:
        """切换到下一个候选通道（递进回退）。成功返回 True。"""
        nxt = self._candidate_idx + 1
        while nxt < len(self._candidates):
            cand = self._candidates[nxt]
            self._candidate_idx = nxt
            self._active = cand
            self._channel = getattr(cand, "channel_name", type(cand).__name__)
            _logger.warning(
                "TTS 切换到音频通道 | channel=%s | reason=%s",
                self._channel,
                reason,
            )
            if self._on_event:
                self._on_event("fallback_activated", {"channel": self._channel, "reason": reason})
            return True
        self._active = None
        self._channel = "none"
        return False

    @staticmethod
    def _is_audio_error(exc: BaseException) -> bool:
        """判断异常是否属于音频设备/播放层错误（应触发通道回退）。"""
        from core.tts.player.core import AudioPlayerError

        if isinstance(exc, AudioPlayerError):
            return True
        if isinstance(exc, OSError):
            return True
        try:
            import sounddevice

            if isinstance(exc, sounddevice.PortAudioError):
                return True
        except ImportError:
            pass
        return False

    # ------------------------------------------------------------------ #
    # 公共异步 API（与 AudioPlayer 同构）
    # ------------------------------------------------------------------ #
    async def feed(self, chunk: bytes) -> None:
        active = self._active
        if active is None:
            if not self._pick_next(reason="active_none"):
                return
            active = self._active
        assert active is not None
        try:
            await active.feed(chunk)
        except Exception as e:
            # 仅对音频层错误做通道回退；其它异常（如 API 错误）向上抛出由调用方重试
            if not self._is_audio_error(e):
                raise
            _logger.error(
                "TTS 播放中断 | channel=%s | error=%s",
                self._channel,
                e,
            )
            # 递进回退：依次尝试后续候选通道重放当前块，直至成功或无更多通道
            while self._pick_next(reason=f"error:{type(e).__name__}"):
                try:
                    assert self._active is not None
                    await self._active.feed(chunk)
                    self._delivered += len(chunk)
                    return
                except Exception:
                    # 该通道也失败，继续尝试下一个候选
                    continue
            return
        self._delivered += len(chunk)

    async def drain(self) -> None:
        if self._active is None:
            return
        try:
            await self._active.drain()
        except Exception as e:
            if self._is_audio_error(e):
                if self._pick_next(reason=f"drain_error:{type(e).__name__}"):
                    try:
                        await self._active.drain()
                        return
                    except Exception:
                        # 下一通道 drain 仍失败，继续向上抛出
                        pass
            raise

    async def play_stream(self, chunks: AsyncIterator[bytes]) -> None:
        """接收异步迭代器并完整播放，等价于逐块 feed 后 drain。"""
        async for chunk in chunks:
            await self.feed(chunk)
        await self.drain()

    async def aclose(self) -> None:
        """关闭所有备用通道。

        主通道（primary）由调用方以 ``async with`` 长期持有，此处不关闭，
        避免多次 speak 之间主播放器被意外释放。
        """
        for sink in self._fallbacks:
            try:
                await sink.aclose()
            except Exception as e:
                name = getattr(sink, "channel_name", "?")
                _logger.debug("音频通道关闭异常 | channel=%s | error=%s", name, e)

    async def __aenter__(self) -> "ResilientAudioPlayer":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


__all__ = [
    "AudioSink",
    "FileAudioSink",
    "ResilientAudioPlayer",
    "LifecycleCallback",
]
