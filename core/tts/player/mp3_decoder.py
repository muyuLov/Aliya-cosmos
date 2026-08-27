"""MP3 流式解码器：持有常驻 ffmpeg 进程，通过管道连续读写实现零批次延迟解码。"""

from __future__ import annotations

import json
import os
import queue
import subprocess as sp
import threading

import imageio_ffmpeg

from core.logger import get_logger

_logger = get_logger(__name__)


class Mp3StreamDecoder:
    """MP3 流式解码器：持有一个常驻 ffmpeg 进程，通过管道连续读写，
    避免批次解码的累积延迟，使口型同步能尽早获得音量数据。

    工作方式：
    - 进程启动后立即开始读取 stdout 写入内部队列
    - decode() 将 MP3 数据写入 stdin，然后读取当前累积的 PCM
    - close() 关闭管道并等待进程退出
    """

    _PCM_READ_SIZE = 8192  # 每次从 stdout 读取的字节数

    def __init__(self) -> None:
        self._proc: sp.Popen | None = None
        self._pcm_queue: queue.Queue[bytes | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._started = False

    # ── ffmpeg 路径查找 ──────────────────────────────────────────────

    @staticmethod
    def _find_ffmpeg() -> str:
        try:
            import portable_ffmpeg

            ffmpeg_path, _ = portable_ffmpeg.get_ffmpeg()
            if ffmpeg_path:
                return str(ffmpeg_path)
        except ImportError:
            pass
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _find_ffprobe(mp3_data: bytes) -> tuple[int, int]:
        """探查 MP3 的采样率和声道数（仅首次调用）。"""
        ffprobe_path = None
        try:
            import portable_ffmpeg

            _, ffprobe_path = portable_ffmpeg.get_ffmpeg()
        except ImportError:
            pass
        if not ffprobe_path:
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            ffprobe_path = ffmpeg_path.replace("ffmpeg", "ffprobe")
            if not os.path.exists(ffprobe_path):
                base, ext = os.path.splitext(ffmpeg_path)
                ffprobe_path = f"{base.replace('ffmpeg', 'ffprobe')}{ext}"
        try:
            proc = sp.Popen(
                [
                    ffprobe_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-print_format",
                    "json",
                    "-show_streams",
                    "-i",
                    "pipe:0",
                ],
                stdin=sp.PIPE,
                stdout=sp.PIPE,
                stderr=sp.PIPE,
            )
            stdout, _ = proc.communicate(input=mp3_data, timeout=10)
            if proc.returncode != 0:
                raise ValueError("ffprobe 返回非零")
            info = json.loads(stdout)
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "audio":
                    return int(stream.get("sample_rate", 24000)), int(stream.get("channels", 1))
            raise ValueError("未找到音频流")
        except Exception:
            _logger.warning("MP3 探查失败，回退到 24000Hz/1ch")
            return 24000, 1

    # ── 进程生命周期 ─────────────────────────────────────────────────

    def start(self, probe_mp3: bytes) -> tuple[int, int]:
        """启动 ffmpeg 子进程 + stdout 读取线程。

        Args:
            probe_mp3: 用于探查采样率和声道数的 MP3 前缀数据。

        Returns:
            (sample_rate, channels)
        """
        if self._started:
            raise RuntimeError("流式解码器已启动")

        sr, ch = self._find_ffprobe(probe_mp3)
        ffmpeg_path = self._find_ffmpeg()

        self._proc = sp.Popen(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-acodec",
                "pcm_s16le",
                "-f",
                "s16le",
                "-flush_packets",
                "1",  # 降低输出延迟
                "pipe:1",
            ],
            stdin=sp.PIPE,
            stdout=sp.PIPE,
            stderr=sp.PIPE,
        )

        # 读取线程：持续从 stdout 读取 PCM 数据放入队列
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="mp3-stream-reader",
            daemon=True,
        )
        self._reader_thread.start()
        self._started = True
        _logger.debug("MP3 流式解码器已启动 | sample_rate=%d | channels=%d", sr, ch)
        return sr, ch

    def _reader_loop(self) -> None:
        """后台读取线程：将 stdout 的 PCM 数据持续推入队列。"""
        try:
            proc = self._proc
            while proc is not None and proc.poll() is None:
                if proc.stdout is None:
                    break
                data = proc.stdout.read(self._PCM_READ_SIZE)
                if not data:
                    break
                self._pcm_queue.put(data)
        except Exception:
            pass
        finally:
            self._pcm_queue.put(None)  # 标记结束

    def decode(self, mp3_data: bytes) -> bytes | None:
        """向 ffmpeg 写入 MP3 数据，读取当前累积的 PCM 输出。

        Args:
            mp3_data: MP3 音频字节。

        Returns:
            解码后的 PCM 字节（s16le），不足一帧时返回空字节串。
        """
        if not self._started or self._proc is None:
            return None
        try:
            stdin = self._proc.stdin
            if stdin is None:
                return None
            if mp3_data:
                stdin.write(mp3_data)
                stdin.flush()

            # 收集当前队列中所有 PCM 数据（非阻塞）
            chunks: list[bytes] = []
            while True:
                try:
                    item = self._pcm_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:  # 结束标记
                    break
                chunks.append(item)
            return b"".join(chunks) if chunks else b""
        except Exception as e:
            _logger.warning("MP3 流式解码异常: %s", e)
            return None

    def flush(self) -> bytes | None:
        """关闭 stdin 让 ffmpeg 处理完剩余数据，读取所有残留 PCM。

        超时策略：总阻塞不超过 5 秒（reader 线程序列超时后直接 kill 进程），
        避免 join + wait 双重超时累积阻塞线程池。
        """
        if not self._started or self._proc is None:
            return None
        try:
            stdin = self._proc.stdin
            if stdin is not None:
                stdin.close()
        except Exception:
            pass
        # 等待读取线程结束（最多 5 秒）
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)
        # 收集残留
        chunks: list[bytes] = []
        while True:
            try:
                item = self._pcm_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                continue
            chunks.append(item)
        # reader 线程结束后立即 kill 进程，不再额外等待 5 秒
        try:
            self._proc.kill()
            self._proc.wait(timeout=3)
        except Exception:
            pass
        return b"".join(chunks) if chunks else b""

    def close(self) -> None:
        """终止进程并清理资源。"""
        self._started = False
        if self._proc is not None:
            try:
                stdin = self._proc.stdin
                if stdin is not None:
                    stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        self._reader_thread = None
        # 清空 PCM 队列
        while True:
            try:
                self._pcm_queue.get_nowait()
            except queue.Empty:
                break
