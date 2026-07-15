"""TTS 模块常量定义"""

from __future__ import annotations

# 哨兵对象：用于标记队列结束
SENTINEL = object()

# TTSService 配置
DEFAULT_PREFETCH_QUEUE_SIZE = 16  # 预取队列大小
DEFAULT_MAX_CONCURRENT_CREATES = 10  # 最大并发创建数
DEFAULT_PREFETCH_WINDOW = 3  # 滑动窗口预取段数（减少资源浪费，支持快速打断）

# TTS 音频缓存配置
DEFAULT_CACHE_ENABLED = True  # 默认启用音频缓存，复用重复文本的合成结果
DEFAULT_CACHE_MAX_AGE = 7 * 24 * 3600  # 缓存有效期（秒），默认 7 天

# AudioPlayer 配置
DEFAULT_PLAY_QUEUE_SIZE = 32  # 播放队列大小
DEFAULT_FRAMES_PER_BUFFER = 1024  # pyaudio 每次写入帧数
WAV_DETECT_SIZE = 4096  # WAV 头部检测缓冲区上限（含扩展 chunk）
