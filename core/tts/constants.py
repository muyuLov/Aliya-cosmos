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
WAV_HEADER_SIZE = 44  # 标准 WAV 头字节数（无扩展 chunk）
WAV_DETECT_SIZE = 4096  # WAV 头部检测缓冲区上限（含扩展 chunk）
