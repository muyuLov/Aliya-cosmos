"""测试情绪引擎 Part A：emotion_state / smoother / observer / tone_injector / engine"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.emotion.emotion_state import (
    EMOTION_ALIASES,
    VAD_EMOTIONS,
    EmotionState,
    normalize_emotion,
)
from agent.emotion.smoother import BASELINE, dominant_emotion, smooth_feeling
from agent.emotion.tone_injector import ToneInjector, _EMOTION_TONE_MAP


# ── emotion_state ──────────────────────────────────────────────


class TestEmotionState:
    def test_vad_emotions_count(self):
        assert len(VAD_EMOTIONS) == 18

    def test_aliases_anger(self):
        assert EMOTION_ALIASES["angry"] == "anger"

    def test_normalize_canonical(self):
        for label in VAD_EMOTIONS:
            assert normalize_emotion(label) == label

    def test_normalize_alias(self):
        assert normalize_emotion("angry") == "anger"
        assert normalize_emotion("ANGRY") == "anger"

    def test_normalize_invalid(self):
        assert normalize_emotion("fantastic") is None
        assert normalize_emotion("") is None

    def test_emotion_state_defaults(self):
        state = EmotionState()
        assert state.dominant == "neutral"
        assert state.scores == {}

    def test_emotion_state_with_scores(self):
        state = EmotionState(dominant="happy", scores={"happy": 0.9, "sad": 0.1})
        assert state.dominant == "happy"
        assert state.scores["happy"] == 0.9


# ── smoother ──────────────────────────────────────────────────


class TestSmoother:
    def test_baseline_all_zero_five(self):
        for label in VAD_EMOTIONS:
            assert BASELINE[label] == 0.5

    def test_smooth_from_none_uses_baseline(self):
        raw = {e: 0.0 for e in VAD_EMOTIONS}
        raw["happy"] = 1.0
        result = smooth_feeling(raw)
        # 从 0.5 向 1.0 平滑：0.5 * (1-0.6) + 1.0 * 0.6 = 0.8
        assert 0.0 <= result["happy"] <= 1.0
        assert result["happy"] > 0.5

    def test_smooth_with_prev(self):
        prev = {e: 0.5 for e in VAD_EMOTIONS}
        prev["sad"] = 0.8
        raw = {e: 0.0 for e in VAD_EMOTIONS}
        raw["sad"] = 1.0
        result = smooth_feeling(raw, prev)
        # 普通标签: alpha=0.6
        assert result["neutral"] < 0.5  # prev=0.5, raw=0.0 → 0.5*(0.4)+0 = 0.2
        # sad 是 fast_rise: alpha_fast=0.85
        # 0.8*(0.15) + 1.0*0.85 = 0.12 + 0.85 = 0.97
        assert result["sad"] > 0.8

    def test_clamping(self):
        raw = {e: 10.0 for e in VAD_EMOTIONS}
        result = smooth_feeling(raw)
        for v in result.values():
            assert 0.0 <= v <= 1.0

    def test_dominant_emotion(self):
        scores = {e: 0.0 for e in VAD_EMOTIONS}
        scores["happy"] = 0.9
        scores["calm"] = 0.5
        assert dominant_emotion(scores) == "happy"


# ── tone_injector ─────────────────────────────────────────────


class TestToneInjector:
    def test_all_emotions_have_tone(self):
        injector = ToneInjector()
        for label in VAD_EMOTIONS:
            state = EmotionState(dominant=label, scores={label: 0.5})
            patch = injector.build_patch(state)
            assert len(patch) > 0
            assert "情绪基调" in patch

    def test_high_intensity_adds_percentage(self):
        injector = ToneInjector()
        state = EmotionState(dominant="happy", scores={"happy": 0.85})
        patch = injector.build_patch(state)
        assert "85%" in patch

    def test_low_intensity_no_percentage(self):
        injector = ToneInjector()
        state = EmotionState(dominant="happy", scores={"happy": 0.3})
        patch = injector.build_patch(state)
        assert "%" not in patch

    def test_tone_map_matches_vad_count(self):
        """每个 VAD 标签都有对应语气映射"""
        assert len(_EMOTION_TONE_MAP) >= len(VAD_EMOTIONS)


# ── observer (mock LLM) ───────────────────────────────────────


class TestEmotionObserver:
    @pytest.mark.asyncio
    async def test_observe_calls_llm(self):
        from agent.emotion.observer import EmotionObserver

        provider = MagicMock()
        provider.model = "test-model"
        provider.async_chat_completion = AsyncMock()

        # 构造一个合法的 JSON 响应
        scores = {e: 0.1 for e in VAD_EMOTIONS}
        scores["happy"] = 0.8
        provider.async_chat_completion.return_value = MagicMock(
            content=json.dumps(scores)
        )

        observer = EmotionObserver(provider)
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀！"},
        ]
        state = await observer.observe(messages)

        provider.async_chat_completion.assert_awaited_once()
        assert state.dominant == "happy"
        assert state.scores["happy"] > 0.5

    @pytest.mark.asyncio
    async def test_observe_empty_messages(self):
        from agent.emotion.observer import EmotionObserver

        provider = MagicMock()
        provider.async_chat_completion = AsyncMock()
        observer = EmotionObserver(provider)
        state = await observer.observe([])
        # 空消息时返回当前状态（默认 neutral）
        assert state.dominant == "neutral"
        provider.async_chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_observe_llm_failure_degrades(self):
        from agent.emotion.observer import EmotionObserver

        provider = MagicMock()
        provider.model = "test-model"
        provider.async_chat_completion = AsyncMock(side_effect=RuntimeError("LLM down"))

        observer = EmotionObserver(provider)
        messages = [{"role": "user", "content": "测试"}]
        state = await observer.observe(messages)
        # 失败后降级到 neutral
        assert state.dominant == "neutral"

    @pytest.mark.asyncio
    async def test_parse_scores_with_markdown_block(self):
        from agent.emotion.observer import EmotionObserver

        content = '```json\n{"happy": 0.8, "sad": 0.2}\n```'
        scores = EmotionObserver._parse_scores(content)
        assert scores["happy"] == 0.8
        assert scores["sad"] == 0.2

    @pytest.mark.asyncio
    async def test_parse_scores_with_angry_alias(self):
        from agent.emotion.observer import EmotionObserver

        content = '{"angry": 0.7, "happy": 0.3}'
        scores = EmotionObserver._parse_scores(content)
        assert scores["anger"] == 0.7

    @pytest.mark.asyncio
    async def test_parse_scores_invalid_json(self):
        from agent.emotion.observer import EmotionObserver

        scores = EmotionObserver._parse_scores("not json at all")
        # 降级到默认
        assert scores["neutral"] == 1.0


# ── engine (装配) ─────────────────────────────────────────────


class TestEmotionEngine:
    def test_create_emotion_engine(self):
        from agent.emotion.engine import create_emotion_engine

        provider = MagicMock()
        provider.model = "test-model"
        engine = create_emotion_engine(provider)
        assert engine.current_state.dominant == "neutral"

    @pytest.mark.asyncio
    async def test_on_turn_complete(self):
        from agent.emotion.engine import EmotionEngine

        provider = MagicMock()
        provider.model = "test-model"
        provider.async_chat_completion = AsyncMock()

        scores = {e: 0.1 for e in VAD_EMOTIONS}
        scores["calm"] = 0.9
        provider.async_chat_completion.return_value = MagicMock(
            content=json.dumps(scores)
        )

        engine = EmotionEngine(provider)
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀！"},
        ]
        state = await engine.on_turn_complete(messages)
        assert state.dominant == "calm"

    @pytest.mark.asyncio
    async def test_on_turn_complete_without_service(self):
        from agent.emotion.engine import EmotionEngine

        provider = MagicMock()
        provider.model = "test-model"
        provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content='{"happy": 0.9}')
        )

        engine = EmotionEngine(provider)
        # 不绑定 service，不报错
        state = await engine.on_turn_complete([{"role": "user", "content": "hi"}])
        assert state.dominant == "happy"

    @pytest.mark.asyncio
    async def test_bind_service(self):
        from agent.emotion.engine import EmotionEngine

        provider = MagicMock()
        provider.model = "test-model"
        engine = EmotionEngine(provider)

        mock_service = MagicMock()
        mock_service.set_emotion_patch = AsyncMock()
        engine.bind_service(mock_service)

        provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content='{"neutral": 0.9}')
        )
        await engine.on_turn_complete([{"role": "user", "content": "hi"}])
        mock_service.set_emotion_patch.assert_awaited_once()
