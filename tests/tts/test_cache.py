"""测试 TTS 音频缓存模块：缓存键构建、本地文件缓存、过期清理"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.tts.cache import TTSCache, _build_cache_key
from core.tts.models import TTSRequest, VoiceConfig


class TestBuildCacheKey:
    def test_same_request_same_key(self):
        req1 = TTSRequest(text="hello", speed=1.0)
        req2 = TTSRequest(text="hello", speed=1.0)
        assert _build_cache_key(req1) == _build_cache_key(req2)

    def test_different_text_different_key(self):
        req1 = TTSRequest(text="hello")
        req2 = TTSRequest(text="world")
        assert _build_cache_key(req1) != _build_cache_key(req2)

    def test_voice_config_affects_key(self):
        req = TTSRequest(text="hello")
        vc1 = VoiceConfig(speed=1.0)
        vc2 = VoiceConfig(speed=2.0)
        key1 = _build_cache_key(req, vc1)
        key2 = _build_cache_key(req, vc2)
        assert key1 != key2

    def test_exclude_none_fields(self):
        """None 字段不应影响缓存键"""
        req1 = TTSRequest(text="hello")
        req2 = TTSRequest(text="hello", avatar_id=None)
        assert _build_cache_key(req1) == _build_cache_key(req2)

    def test_key_format(self):
        key = _build_cache_key(TTSRequest(text="test"))
        assert len(key) == 32  # MD5 hex
        assert all(c in "0123456789abcdef" for c in key)


class TestTTSCache:
    @pytest.fixture
    def cache_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "tts_cache"

    @pytest.fixture
    def cache(self, cache_dir: Path) -> TTSCache:
        return TTSCache(cache_dir=cache_dir, enabled=True, max_age_seconds=3600)

    def test_disabled_cache_returns_none(self, cache_dir: Path):
        cache = TTSCache(cache_dir=cache_dir, enabled=False)
        result = cache.get(TTSRequest(text="hi"))
        assert result is None

    def test_set_and_get(self, cache):
        req = TTSRequest(text="hello")
        cache.set(req, b"audio_data")
        result = cache.get(req)
        assert result == b"audio_data"

    def test_get_missing_returns_none(self, cache):
        result = cache.get(TTSRequest(text="nonexistent"))
        assert result is None

    def test_cache_miss_after_expiry(self, cache_dir: Path):
        """短 TTL 过期后 get 返回 None"""
        cache = TTSCache(cache_dir=cache_dir, enabled=True, max_age_seconds=1)
        req = TTSRequest(text="expire_soon")
        cache.set(req, b"data")
        time.sleep(1.5)
        result = cache.get(req)
        assert result is None

    def test_clear(self, cache):
        cache.set(TTSRequest(text="a"), b"aaa")
        cache.set(TTSRequest(text="b"), b"bbb")
        deleted = cache.clear()
        assert deleted == 2
        assert cache.get(TTSRequest(text="a")) is None

    def test_clear_empty_cache(self, cache_dir: Path):
        cache = TTSCache(cache_dir=cache_dir, enabled=True)
        assert cache.clear() == 0

    def test_set_empty_audio_skips_cache(self, cache):
        cache.set(TTSRequest(text="test"), b"")
        assert cache.get(TTSRequest(text="test")) is None

    def test_different_voice_config_different_cache(self, cache):
        req = TTSRequest(text="hello")
        vc1 = VoiceConfig(speed=1.0)
        vc2 = VoiceConfig(speed=2.0)
        cache.set(req, b"speed1", voice_config=vc1)
        result = cache.get(req, voice_config=vc2)
        assert result is None  # 不同配置应不命中
