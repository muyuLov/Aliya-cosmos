"""TTS 模块常量定义"""

from __future__ import annotations

# 哨兵对象：用于标记队列结束
SENTINEL = object()

# TTSService 配置
DEFAULT_PREFETCH_QUEUE_SIZE = 16  # 预取队列大小
DEFAULT_MAX_CONCURRENT_CREATES = 10  # 最大并发创建数
DEFAULT_PREFETCH_WINDOW = 3  # 滑动窗口预取段数（减少资源浪费，支持快速打断）

# AudioPlayer 配置
DEFAULT_PLAY_QUEUE_SIZE = 32  # 播放队列大小
DEFAULT_FRAMES_PER_BUFFER = 1024  # pyaudio 每次写入帧数
WAV_DETECT_SIZE = 4096  # WAV 头部检测缓冲区上限（含扩展 chunk）
MP3_DECODE_THRESHOLD = 16384  # MP3 解码触发阈值（字节），约 2.7 秒 @48kbps
