"""AudioPlayer：流式音频播放器（sounddevice + asyncio 架构）"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from typing import AsyncIterator

import numpy as np
import sounddevice as sd

from core.logger import get_logger
from core.tts.constants import (
    DEFAULT_FRAMES_PER_BUFFER,
    DEFAULT_PLAY_QUEUE_SIZE,
    MP3_DECODE_THRESHOLD,
    WAV_DETECT_SIZE,
)
from core.tts.player.audio_features import AudioFeatures
from core.tts.player.format_detector import RIFF_MAGIC, parse_wav_header
from core.tts.player.mp3_decoder import Mp3StreamDecoder

_logger = get_logger(__name__)

# MP3 同步字检测（0xFF 后跟 0xE? 或 0xF?）
_MP3_SYNC_BYTES = (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"\xff\xf9", b"\xff\xf8")

# sounddevice dtype → 样本字节宽度映射（用于字节对齐计算）
_SD_DTYPE_SAMPLE_WIDTH: dict[str, int] = {
    "float32": 4,
    "int32": 4,
    "int24": 3,
    "int16": 2,
    "int8": 1,
}

# dtype → RMS 归一化峰值（峰值 = 2^(bits-1) 或 1.0 for float）
_PEAK_MAX: dict[str, float] = {
    "float32": 1.0,
    "int32": 2147483648.0,
    "int16": 32768.0,
    "int8": 128.0,
}

# 标记播放队列结束
_SENTINEL = object()


class AudioPlayerError(Exception):
    """音频播放器异常。"""

    def __init__(self, reason: str, cause: Exception | None = None) -> None:
        super().__init__(f"音频播放失败: {reason}")
        self.cause = cause


class AudioPlayer:
    """
    流式音频播放器，支持边接收音频块边实时播放（sounddevice + asyncio 架构）。

    使用 threading.Queue 桥接异步生产者与同步 sounddevice 消费者，
    不阻塞事件循环。sounddevice 在独立线程中管理音频流。

    格式检测：首个 chunk 到达时检查前 4 字节——非 RIFF 立即以 PCM 模式启动，
    RIFF 则缓冲至 WAV_DETECT_SIZE 字节后解析头部。

    Args:
        sample_rate: PCM 模式采样率（Hz），默认 32000。
        channels: PCM 模式声道数，默认 1。
        pcm_format: PCM 格式，支持 ``"float32"``（默认，AstraTTS 输出格式）、
            ``"int16"``、``"int32"``、``"int8"``。
        frames_per_buffer: sounddevice 每次写入帧数，影响延迟与稳定性，默认 1024。
        play_queue_size: 播放队列大小，控制缓冲区容量，默认 32。
        queue_timeout: 播放队列等待超时（秒），用于检测异常情况，默认 300 秒（5 分钟）。
            设置为 None 表示无限等待（不推荐，可能导致线程无法退出）。
    """

    def __init__(
        self,
        sample_rate: int = 32000,
        channels: int = 1,
        pcm_format: str = "float32",
        frames_per_buffer: int = DEFAULT_FRAMES_PER_BUFFER,
        play_queue_size: int = DEFAULT_PLAY_QUEUE_SIZE,
        queue_timeout: float | None = 300.0,
    ) -> None:
        from core.tts.validation import validate_audio_player_config

        validate_audio_player_config(
            sample_rate, channels, pcm_format, frames_per_buffer, play_queue_size, queue_timeout
        )

        self._default_sample_rate = sample_rate
        self._default_channels = channels
        self._pcm_format = pcm_format
        self._frames_per_buffer = frames_per_buffer
        self._play_queue_size = play_queue_size
        self._queue_timeout = queue_timeout

        # sounddevice OutputStream 实例（播放线程持有）
        self._stream: sd.OutputStream | None = None

        # 播放线程
        self._play_thread: threading.Thread | None = None

        # 播放队列（threading.Queue，桥接异步生产者与同步消费者）
        self._queue: queue.Queue[object] = queue.Queue(maxsize=play_queue_size)

        # 格式检测状态
        self._header_buf = bytearray()
        self._is_wav: bool | None = None  # None=未检测, True=WAV, False=PCM
        self._is_mp3: bool | None = None  # None=未检测, True=MP3, False=其他
        self._mp3_buffer = bytearray()  # MP3 格式检测累积缓冲
        self._mp3_pending = bytearray()  # 后续 MP3 块累积缓冲（批量送入解码器）

        # 当前流实际使用的格式信息（用于字节对齐）
        self._current_sample_rate: int | None = None
        self._current_channels: int | None = None
        self._current_sample_width: int | None = None
        self._current_dtype: str | None = None

        # 播放错误（从播放线程传递到调用方协程）
        self._play_error: Exception | None = None

        # drain 锁（确保串行执行）
        self._drain_lock = asyncio.Lock()

        # 口型同步：音频特征（由播放线程写入，外部协程读取）
        self._last_volume: float = 0.0  # 归一化 RMS 音量 (0~1)
        self._last_centroid: float = 0.5  # 频谱质心比 (0~1)，低→元音(ah) 高→辅音(ss)
        self._last_zcr: float = 0.0  # 过零率归一化 (0~1)，低→浊音 高→清音

        # MP3 流式解码器（常驻 ffmpeg 进程，管道读写，零批次延迟）
        self._mp3_stream_decoder: Mp3StreamDecoder | None = None

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #

    @property
    def last_volume(self) -> float:
        """最近一个音频缓冲区的归一化 RMS 音量（0.0~1.0），用于口型同步等外部驱动。"""
        return self._last_volume

    # ------------------------------------------------------------------ #
    # 公共异步 API
    # ------------------------------------------------------------------ #

    async def feed(self, chunk: bytes) -> None:
        """
        送入一个音频块，首个 chunk 触发格式检测并启动播放线程。

        格式检测策略：
        - PCM 模式：检测到非 RIFF 后立即启动播放，延迟最小。
        - WAV 模式：需积累至少 WAV_DETECT_SIZE 字节解析头部，可能产生初始延迟。
          这是必要的权衡，因为 WAV 头部可能包含多个 chunk（fmt、LIST、bext、data 等）。
        - 多段 WAV：当 PCM 流中检测到新的 RIFF 魔数时，说明新一段 WAV 开始，
          自动等待当前段播放完毕后重置格式状态，避免 WAV 头被当作 PCM 数据播放。

        Args:
            chunk: 音频字节块。

        Raises:
            AudioPlayerError: 播放线程已报错时抛出。
        """
        async with self._drain_lock:
            self._check_play_error()

            # ---- 模式1：已确定为 MP3，流式管道解码 ----
            if self._is_mp3 is True:
                decoder = self._mp3_stream_decoder
                if decoder is None:
                    loop = asyncio.get_running_loop()
                    probe_data = bytes(self._mp3_buffer) + chunk
                    decoder = Mp3StreamDecoder()
                    sr, ch = await loop.run_in_executor(
                        None,
                        decoder.start,
                        probe_data,
                    )
                    self._mp3_stream_decoder = decoder
                    self._mp3_buffer.clear()
                    self._open_stream(sr, ch, 2, "int16")
                    pcm = await loop.run_in_executor(
                        None,
                        decoder.decode,
                        probe_data,
                    )
                else:
                    self._mp3_pending += chunk
                    if len(self._mp3_pending) < MP3_DECODE_THRESHOLD:
                        return
                    loop = asyncio.get_running_loop()
                    batch = bytes(self._mp3_pending)
                    self._mp3_pending.clear()
                    pcm = await loop.run_in_executor(
                        None,
                        decoder.decode,
                        batch,
                    )

                if pcm:
                    await self._enqueue(pcm)
                return

            # ---- 模式2：已确定为 PCM，检查是否遇到新段落 ----
            if self._is_wav is False and self._is_mp3 is False:
                if chunk[:4] == RIFF_MAGIC:
                    await self._flush_and_reset()
                elif any(chunk[:2] == sync for sync in _MP3_SYNC_BYTES):
                    await self._flush_and_reset()
                else:
                    await self._enqueue(chunk)
                return

            self._header_buf.extend(chunk)

            if self._is_wav is None and self._is_mp3 is None:
                if len(self._header_buf) < 4:
                    return
                if any(self._header_buf[:2] == sync for sync in _MP3_SYNC_BYTES):
                    _logger.debug("检测到 MP3 格式，开始解码")
                    self._is_mp3 = True
                    self._mp3_buffer.extend(self._header_buf)
                    self._header_buf.clear()
                    return
                if self._header_buf[:4] == RIFF_MAGIC:
                    self._is_wav = True
                else:
                    self._is_wav = False
                    self._is_mp3 = False
                    self._start_pcm()
                    await self._enqueue(bytes(self._header_buf))
                    self._header_buf.clear()
                return

            # ---- 模式3：WAV 头部解析 ----
            if self._is_wav is True:
                if len(self._header_buf) >= 44:
                    data = bytes(self._header_buf)
                    try:
                        fmt_info = parse_wav_header(data)
                        pcm_start = fmt_info.pcm_start
                        _logger.debug(
                            "WAV 头部解析成功（增量） | buffered_bytes=%d | sample_rate=%d | channels=%d",
                            len(self._header_buf),
                            fmt_info.sample_rate,
                            fmt_info.channels,
                        )
                        self._open_stream(
                            fmt_info.sample_rate,
                            fmt_info.channels,
                            fmt_info.sample_width,
                            fmt_info.pa_format,
                        )
                        self._is_wav = False
                        self._is_mp3 = False
                        data = bytes(self._header_buf[pcm_start:])
                        self._header_buf.clear()
                        if data:
                            await self._enqueue(data)
                        return
                    except Exception as e:
                        if len(self._header_buf) < WAV_DETECT_SIZE:
                            _logger.debug(
                                "WAV 头部不完整，继续缓冲 | buffered_bytes=%d | reason=%s",
                                len(self._header_buf),
                                e,
                            )
                            return
                        _logger.warning(
                            "WAV 头部解析失败，回退到 PCM | buffered_bytes=%d | reason=%s",
                            len(self._header_buf),
                            e,
                        )
                        self._is_wav = False
                        self._is_mp3 = False
                        self._start_pcm()
                        await self._enqueue(bytes(self._header_buf))
                        self._header_buf.clear()
                        return

    async def _flush_and_reset(self) -> None:
        """等待当前段播放完毕，然后重置格式状态以接受下一段音频。"""
        if self._play_thread is not None:
            await self._enqueue(_SENTINEL)
            # 轮询代替 thread.join，不占线程池
            while self._play_thread.is_alive():
                await asyncio.sleep(0.005)
            self._check_play_error()

        self._play_thread = None
        self._close_stream()
        if self._mp3_stream_decoder is not None:
            self._mp3_stream_decoder.close()
            self._mp3_stream_decoder = None
        self._is_wav = None
        self._is_mp3 = None
        self._header_buf.clear()
        self._mp3_buffer.clear()
        self._mp3_pending.clear()
        self._play_error = None
        self._clear_queue()
        self._queue = queue.Queue(maxsize=self._play_queue_size)
        _logger.debug("段边界重置完成，准备接收下一段音频")

    async def drain(self) -> None:
        """
        等待所有已送入的音频块播放完毕，完成后重置状态供下次使用。

        边界情况处理：
        - 如果 WAV 文件总大小 < 512 字节，会在此处强制完成格式检测并播放。
        - 如果 PCM 数据不足触发检测阈值，会以默认参数启动播放。
        - 如果残留数据 < 4 字节，无法确定格式且无法构成有效音频样本，会被丢弃。

        并发安全：使用 asyncio.Lock 确保多个协程调用时串行执行，避免资源泄漏。
        """
        async with self._drain_lock:
            # 处理残留的 MP3 数据（先排空累积缓冲再 flush 流式解码器）
            if self._is_mp3 is True and self._mp3_stream_decoder is not None:
                loop = asyncio.get_running_loop()
                # 送入批处理缓冲中未解码的数据
                if self._mp3_pending:
                    pending = bytes(self._mp3_pending)
                    self._mp3_pending.clear()
                    pcm_data = await loop.run_in_executor(
                        None,
                        self._mp3_stream_decoder.decode,
                        pending,
                    )
                    if pcm_data:
                        await self._enqueue(pcm_data)
                pcm_data = await loop.run_in_executor(
                    None,
                    self._mp3_stream_decoder.flush,
                )
                if pcm_data:
                    await self._enqueue(pcm_data)

            if self._header_buf:
                buf_size = len(self._header_buf)

                if self._is_wav is None and self._is_mp3 is None and buf_size < 4:
                    _logger.warning(
                        "残留数据不足 4 字节，无法确定格式，已丢弃 | bytes=%d",
                        buf_size,
                    )
                    self._header_buf.clear()
                else:
                    _logger.debug(
                        "强制完成格式检测 | buffered_bytes=%d | is_wav=%s | is_mp3=%s",
                        buf_size,
                        self._is_wav,
                        self._is_mp3,
                    )
                    if self._is_wav is None and self._is_mp3 is None:
                        if any(self._header_buf[:2] == sync for sync in _MP3_SYNC_BYTES):
                            self._is_mp3 = True
                            # 启动流式解码器处理残留数据
                            decoder = Mp3StreamDecoder()
                            try:
                                sr, ch = decoder.start(bytes(self._header_buf))
                                self._mp3_stream_decoder = decoder
                                self._open_stream(sr, ch, 2, "int16")
                                pcm = decoder.decode(bytes(self._header_buf))
                                self._header_buf.clear()
                                if pcm:
                                    await self._enqueue(pcm)
                            except Exception:
                                decoder.close()
                                raise
                        else:
                            self._is_wav = False
                            self._is_mp3 = False
                            self._start_pcm()
                            data = bytes(self._header_buf)
                            self._header_buf.clear()
                            if data:
                                await self._enqueue(data)
                    elif self._is_wav is True:
                        pcm_start = self._start_wav()
                        self._is_wav = False
                        self._is_mp3 = False
                        data = bytes(self._header_buf[pcm_start:])
                        self._header_buf.clear()
                        if data:
                            await self._enqueue(data)

            if self._play_thread is not None:
                await self._enqueue(_SENTINEL)
                while self._play_thread.is_alive():
                    await asyncio.sleep(0.005)
                self._check_play_error()

            # 重置播放状态
            self._play_thread = None
            self._close_stream()
            if self._mp3_stream_decoder is not None:
                self._mp3_stream_decoder.close()
                self._mp3_stream_decoder = None
            self._is_wav = None
            self._is_mp3 = None
            self._header_buf.clear()
            self._mp3_buffer.clear()
            self._mp3_pending.clear()
            self._play_error = None
            self._clear_queue()
            self._queue = queue.Queue(maxsize=self._play_queue_size)

    async def play_stream(self, chunks: AsyncIterator[bytes]) -> None:
        """接收异步迭代器并完整播放，等价于逐块 feed 后 drain。"""
        async for chunk in chunks:
            await self.feed(chunk)
        await self.drain()

    async def aclose(self) -> None:
        """释放 sounddevice 资源，建议通过 ``async with`` 自动管理。"""
        # 通知播放线程退出
        self._clear_queue()
        try:
            self._queue.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        if self._play_thread and self._play_thread.is_alive():
            deadline = time.monotonic() + 2.0
            while self._play_thread.is_alive() and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
        self._close_stream()
        if self._mp3_stream_decoder is not None:
            self._mp3_stream_decoder.close()
            self._mp3_stream_decoder = None
        _logger.debug("AudioPlayer 已关闭")

    async def __aenter__(self) -> "AudioPlayer":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # 内部方法
    # ------------------------------------------------------------------ #

    async def _enqueue(self, item: object) -> None:
        """放入播放队列，队列满时在线程池中等待，不阻塞事件循环。"""
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # 不占线程池：事件循环轮询代替阻塞 put，响应更灵敏
            while True:
                try:
                    self._queue.put_nowait(item)
                    return
                except queue.Full:
                    await asyncio.sleep(0.005)

    def _open_stream(self, sample_rate: int, channels: int, sample_width: int, dtype: str) -> None:
        """打开 sounddevice OutputStream 并启动播放线程。"""
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype=dtype,
            blocksize=self._frames_per_buffer,
        )

        self._current_sample_rate = sample_rate
        self._current_channels = channels
        self._current_sample_width = sample_width
        self._current_dtype = dtype

        try:
            self._stream.start()
        except Exception:
            # 启动失败需关闭已创建的流，避免 sounddevice 资源泄漏
            self._close_stream()
            raise

        self._play_thread = threading.Thread(
            target=self._play_loop,
            name="tts-audio-player",
            daemon=True,
        )
        self._play_thread.start()

    def _start_pcm(self) -> None:
        """以构造参数启动 PCM 播放线程。"""
        _logger.debug(
            "检测到原始 PCM 格式 | sample_rate=%d | channels=%d | pcm_format=%s",
            self._default_sample_rate,
            self._default_channels,
            self._pcm_format,
        )
        self._open_stream(
            self._default_sample_rate,
            self._default_channels,
            _SD_DTYPE_SAMPLE_WIDTH.get(self._pcm_format, 4),
            self._pcm_format,
        )

    def _start_wav(self) -> int:
        """解析 WAV 头部，启动播放线程，返回 PCM 数据起始偏移。"""
        data = bytes(self._header_buf)
        try:
            fmt_info = parse_wav_header(data)
            _logger.debug(
                "检测到 WAV 格式 | sample_rate=%d | channels=%d | sample_width=%d | pcm_start=%d",
                fmt_info.sample_rate,
                fmt_info.channels,
                fmt_info.sample_width,
                fmt_info.pcm_start,
            )
            self._open_stream(
                fmt_info.sample_rate, fmt_info.channels, fmt_info.sample_width, fmt_info.pa_format
            )
            return fmt_info.pcm_start
        except Exception as e:
            _logger.warning("WAV 头部解析失败，丢弃头部数据 | reason=%s", e)
            self._start_pcm()
            return len(data)

    def _play_loop(self) -> None:
        """
        播放线程主循环：从队列取数据写入 sounddevice 流。

        字节对齐：积累不完整的样本帧，确保每次写入 sounddevice 的数据都是完整帧。
        """
        assert self._stream is not None

        dtype = self._current_dtype or self._pcm_format
        sample_width = _SD_DTYPE_SAMPLE_WIDTH.get(dtype, 4)
        channels = self._current_channels or self._default_channels
        frame_size = sample_width * channels

        align_buf = bytearray()

        # 队列超时：默认 300s，None 时轮询 1s
        _qtimeout = self._queue_timeout
        get_timeout = _qtimeout if _qtimeout is not None else 1.0

        try:
            while True:
                try:
                    item = self._queue.get(timeout=get_timeout)
                except queue.Empty:
                    if _qtimeout is None:
                        continue
                    _logger.warning(
                        "播放队列等待超时，退出播放线程 | timeout=%.1fs",
                        _qtimeout,
                    )
                    self._play_error = TimeoutError(f"播放队列等待超时 {_qtimeout} 秒")
                    break

                if item is _SENTINEL:
                    # 丢弃不完整帧后退出
                    if align_buf:
                        remainder = len(align_buf) % frame_size
                        if remainder:
                            _logger.debug(
                                "丢弃不完整帧 | discarded_bytes=%d | frame_size=%d",
                                remainder,
                                frame_size,
                            )
                            del align_buf[-remainder:]
                        if align_buf:
                            self._write_audio(bytes(align_buf))
                        align_buf.clear()
                    break

                # item 一定是 bytes（_SENTINEL 已在上方处理）
                align_buf.extend(item)  # type: ignore[arg-type]

                writable = (len(align_buf) // frame_size) * frame_size
                if writable == 0:
                    continue

                to_write = bytes(align_buf[:writable])
                del align_buf[:writable]
                self._write_audio(to_write)

        except OSError as e:
            _logger.error("播放线程异常", exc_info=True)
            self._play_error = e
        finally:
            _logger.debug("音频播放线程已退出")

    def _write_audio(self, audio_data: bytes) -> None:
        """
        将字节数据写入 sounddevice 流，同时提取 RMS 音量用于口型同步。

        正确处理 int24 格式：先将 3 字节/样本转换为 4 字节 int32，再写入流。
        """
        stream = self._stream
        if not audio_data or stream is None:
            self._last_volume = 0.0
            self._last_centroid = 0.5
            self._last_zcr = 0.0
            return

        dtype = self._current_dtype or self._pcm_format

        # ---- int24 → int32 转换 ----
        if dtype == "int24":
            uint8_data = np.frombuffer(audio_data, dtype=np.uint8)
            n_samples = len(uint8_data) // 3
            if n_samples == 0:
                self._last_volume = 0.0
                return
            uint8_data = uint8_data[: n_samples * 3]
            b0 = uint8_data[::3].astype(np.int32)
            b1 = uint8_data[1::3].astype(np.int32) << 8
            b2 = uint8_data[2::3].astype(np.int32) << 16
            samples_i32 = b0 | b1 | b2
            samples_i32[samples_i32 >= 0x800000] -= 0x1000000
            self._compute_and_update(samples_i32.astype(np.float64), 8388608.0)
            stream.write(samples_i32)
            return

        arr = np.frombuffer(audio_data, dtype=np.dtype(dtype))
        peak_max = _PEAK_MAX.get(dtype, 1.0)
        self._compute_and_update(arr.astype(np.float64), peak_max)
        stream.write(arr)

    def _compute_and_update(self, samples: np.ndarray, peak_max: float) -> None:
        """计算音频特征并更新口型同步属性。"""
        sr = self._current_sample_rate or self._default_sample_rate
        # 归一化到 [-1, 1] 供 AudioFeatures 计算
        normalized = samples / peak_max if peak_max > 0 else samples
        features = AudioFeatures.compute(normalized, sr)
        self._last_volume = features.volume
        self._last_centroid = features.centroid
        self._last_zcr = features.zcr

    def _close_stream(self) -> None:
        """关闭 sounddevice 流并置空引用。"""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except OSError as exc:
                _logger.warning("sounddevice 流关闭失败 | reason=%s", exc, exc_info=True)
            self._stream = None

    def _check_play_error(self) -> None:
        """若播放线程已报错，将异常重新抛出到调用方协程。"""
        if self._play_error:
            raise AudioPlayerError("播放线程异常", cause=self._play_error)

    def _clear_queue(self) -> None:
        """清空队列中的所有残留数据，防止内存泄漏。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
