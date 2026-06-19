"""TTS 音频缓存模块：基于文本 hash 的音频结果缓存"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from core.logger import get_logger
from core.tts.models import TTSRequest, VoiceConfig

_logger = get_logger(__name__)

# 缓存目录
DEFAULT_CACHE_DIR = Path("data/cache/tts")
# 缓存文件扩展名
AUDIO_EXT = ".audio"


def _build_cache_key(request: TTSRequest, voice_config: VoiceConfig | None = None) -> str:
    """
    基于文本内容和音色参数构建缓存键（MD5）。

    Args:
        request: TTS 请求。
        voice_config: 音色配置。

    Returns:
        32 位 MD5 十六进制字符串。
    """
    # 使用稳定的序列化键（排除 None 值 + 排序键）
    raw = request.model_dump(exclude_none=True)
    if voice_config:
        vc = voice_config.model_dump(exclude_none=True)
        # 合并，request 中的显式值优先
        merged = {**vc, **raw}
    else:
        merged = raw

    # 稳定序列化
    payload = json.dumps(merged, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


class TTSCache:
    """
    TTS 音频缓存，支持本地文件存储和可选 Redis 存储。

    Args:
        cache_dir: 本地缓存目录，默认 ``data/cache/tts``。
        redis_url: Redis 连接 URL（如 ``redis://localhost:6379/0``），为 None 时禁用 Redis。
        enabled: 是否启用缓存，默认 True。
        max_age_seconds: 缓存最大有效期（秒），默认 7 天。
    """

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        redis_url: str | None = None,
        enabled: bool = True,
        max_age_seconds: int = 7 * 24 * 3600,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._enabled = enabled
        self._max_age_seconds = max_age_seconds
        self._redis_client: Any = None  # Redis client, lazy init

        # 初始化本地缓存目录
        if self._enabled and not self._cache_dir.exists():
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            _logger.info("TTS 缓存目录已创建: %s", self._cache_dir)

        # 初始化 Redis（如果配置了）
        if redis_url:
            try:
                import redis

                self._redis_client = redis.from_url(redis_url, decode_responses=False)
                _logger.info("TTS Redis 缓存已启用: %s", redis_url)
            except ImportError:
                _logger.warning(
                    "Redis 未安装，缓存将使用本地文件模式 | redis_url=%s",
                    redis_url,
                )

    def _get_file_path(self, cache_key: str) -> Path:
        """获取本地缓存文件路径。"""
        # 使用前 2 位字符作为子目录，避免单目录下文件过多
        subdir = self._cache_dir / cache_key[:2]
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{cache_key}{AUDIO_EXT}"

    def get(self, request: TTSRequest, voice_config: VoiceConfig | None = None) -> bytes | None:
        """
        根据请求查找缓存，返回缓存的音频字节数据。

        Args:
            request: TTS 请求。
            voice_config: 音色配置。

        Returns:
            缓存的音频数据（bytes），缓存未命中时返回 None。
        """
        if not self._enabled:
            return None

        cache_key = _build_cache_key(request, voice_config)

        # 优先 Redis
        if self._redis_client is not None:
            try:
                data = self._redis_client.get(cache_key)
                if data:
                    _logger.debug("TTS 缓存命中（Redis）| key=%s", cache_key)
                    return bytes(data)
            except Exception as e:
                _logger.warning("Redis 缓存读取失败，降级到本地 | error=%s", e)

        # 本地文件缓存
        cache_file = self._get_file_path(cache_key)
        if cache_file.exists():
            import time

            # 检查是否过期
            age = time.time() - cache_file.stat().st_mtime
            if age > self._max_age_seconds:
                _logger.debug("TTS 缓存已过期 | key=%s | age=%.1fs", cache_key, age)
                try:
                    cache_file.unlink()
                except OSError:
                    pass
                return None

            try:
                data = cache_file.read_bytes()
                _logger.debug(
                    "TTS 缓存命中（本地）| key=%s | size=%d bytes",
                    cache_key,
                    len(data),
                )
                # 同时写回 Redis（如果可用）
                if self._redis_client is not None:
                    try:
                        self._redis_client.setex(
                            cache_key,
                            self._max_age_seconds,
                            data,
                        )
                    except Exception:
                        pass
                return data
            except OSError as e:
                _logger.warning("读取缓存文件失败 | file=%s | error=%s", cache_file, e)

        return None

    def set(
        self,
        request: TTSRequest,
        audio_data: bytes,
        voice_config: VoiceConfig | None = None,
    ) -> None:
        """
        将音频数据写入缓存。

        Args:
            request: TTS 请求。
            audio_data: 音频字节数据。
            voice_config: 音色配置。
        """
        if not self._enabled or not audio_data:
            return

        cache_key = _build_cache_key(request, voice_config)

        # 写入 Redis
        if self._redis_client is not None:
            try:
                self._redis_client.setex(
                    cache_key,
                    self._max_age_seconds,
                    audio_data,
                )
            except Exception as e:
                _logger.warning("Redis 缓存写入失败 | error=%s", e)

        # 写入本地文件
        cache_file = self._get_file_path(cache_key)
        try:
            cache_file.write_bytes(audio_data)
            _logger.debug(
                "TTS 缓存已写入 | key=%s | size=%d bytes | file=%s",
                cache_key,
                len(audio_data),
                cache_file,
            )
        except OSError as e:
            _logger.warning("写入缓存文件失败 | file=%s | error=%s", cache_file, e)

    def clear(self) -> int:
        """
        清除所有本地缓存文件。

        Returns:
            删除的文件数量。
        """
        if not self._cache_dir.exists():
            return 0

        count = 0
        for ext in (AUDIO_EXT,):
            for f in self._cache_dir.rglob(f"*{ext}"):
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass

        _logger.info("TTS 缓存已清除 | deleted=%d", count)
        return count

    def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis_client is not None:
            try:
                self._redis_client.close()
            except Exception:
                pass


# ------------------------------------------------------------------ #
# 便捷函数
# ------------------------------------------------------------------ #


def create_cache(
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    redis_url: str | None = None,
    enabled: bool = True,
) -> TTSCache:
    """
    创建 TTSCache 实例。

    Args:
        cache_dir: 本地缓存目录。
        redis_url: Redis 连接 URL。
        enabled: 是否启用缓存。

    Returns:
        TTSCache 实例。
    """
    return TTSCache(
        cache_dir=cache_dir,
        redis_url=redis_url,
        enabled=enabled,
    )


__all__ = ["TTSCache", "create_cache", "_build_cache_key"]
