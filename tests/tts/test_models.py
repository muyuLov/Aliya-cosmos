"""测试 TTS 数据模型：TTSRequest、VoiceConfig、apply_to_request"""

from __future__ import annotations

from core.tts.models import TTSRequest, VoiceConfig


class TestTTSRequest:
    def test_minimal_request(self):
        req = TTSRequest(text="你好")
        assert req.text == "你好"
        assert req.speed is None

    def test_full_request(self):
        req = TTSRequest(
            text="hello", speed=1.5, avatar_id="ava-1", languages=["zh", "en"],
        )
        assert req.speed == 1.5
        assert req.avatar_id == "ava-1"

    def test_speed_range_validated(self):
        import pytest
        with pytest.raises(Exception):
            TTSRequest(text="t", speed=10.0)  # > 5.0

    def test_empty_text_invalid(self):
        import pytest
        with pytest.raises(Exception):
            TTSRequest(text="")  # min_length=1


class TestVoiceConfig:
    def test_default_values(self):
        cfg = VoiceConfig()
        assert cfg.speed == 1.0
        assert cfg.avatar_id is None
        assert cfg.languages is None

    def test_from_config_filters_none(self):
        raw = {"speed": 1.2, "avatar_id": None, "languages": ["zh"]}
        cfg = VoiceConfig.from_config(raw)
        assert cfg.speed == 1.2
        assert cfg.avatar_id is None
        assert cfg.languages == ["zh"]

    def test_apply_to_request_fills_none(self):
        cfg = VoiceConfig(speed=1.2, languages=["en"])
        req = TTSRequest(text="hi")  # speed=None, languages=None
        merged = cfg.apply_to_request(req)
        assert merged.speed == 1.2
        assert merged.languages == ["en"]
        assert merged.text == "hi"

    def test_apply_to_request_does_not_override(self):
        cfg = VoiceConfig(speed=1.2)
        req = TTSRequest(text="hi", speed=0.8)  # speed 显式设置
        merged = cfg.apply_to_request(req)
        assert merged.speed == 0.8  # 保留请求中的值

    def test_apply_to_request_returns_same_if_no_overrides(self):
        """所有字段都是 None 时返回同一实例"""
        cfg = VoiceConfig(speed=None, avatar_id=None, languages=None)
        req = TTSRequest(text="hi")
        merged = cfg.apply_to_request(req)
        assert merged is req  # 无覆盖时返回同一实例（model_copy 优化）
