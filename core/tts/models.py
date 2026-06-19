"""TTS 数据模型"""

from __future__ import annotations

import threading

from pydantic import BaseModel, Field, PrivateAttr


class TTSRequest(BaseModel):
    """TTS 合成请求。"""

    text: str = Field(min_length=1)
    avatar_id: str | None = None
    reference_id: str | None = None
    speed: float | None = Field(default=None, ge=0.1, le=5.0)
    noise_scale: float | None = None
    temperature: float | None = None
    top_k: int | None = Field(default=None, ge=1)
    chunk_size: int | None = Field(default=None, ge=1)
    g2p_priority_mode: int | None = None
    languages: list[str] | None = None


class VoiceConfig(BaseModel):
    """音色默认配置，通常从配置文件加载，None 字段不覆盖请求中的值。"""

    avatar_id: str | None = None
    reference_id: str | None = None
    speed: float | None = Field(default=1.0, ge=0.1, le=5.0)
    noise_scale: float | None = None
    temperature: float | None = None
    top_k: int | None = Field(default=None, ge=1)
    chunk_size: int | None = Field(default=None, ge=1)
    g2p_priority_mode: int | None = None
    languages: list[str] | None = None

    # 缓存 model_dump 结果，避免每次 apply_to_request 都重新序列化
    _dump_cache: dict | None = PrivateAttr(default=None)
    _cache_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    def apply_to_request(self, request: TTSRequest) -> TTSRequest:
        """将配置中的默认值填充到请求中 None 的字段，返回新实例。"""
        # 双重检查锁定模式（Double-Checked Locking）
        if self._dump_cache is None:
            with self._cache_lock:
                if self._dump_cache is None:
                    self._dump_cache = self.model_dump(exclude_none=True)
        overrides = {k: v for k, v in self._dump_cache.items() if getattr(request, k, None) is None}
        return request.model_copy(update=overrides) if overrides else request

    @classmethod
    def from_config(cls, raw: dict) -> "VoiceConfig":
        """从配置字典构建实例，自动过滤 None 值。"""
        return cls(**{k: v for k, v in raw.items() if v is not None})
