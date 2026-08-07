"""测试 StyleSwitcher：规则快速路径与 LLM 兜底"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llm.providers.base import LLMProvider
from agent.prompts.style_switcher import StyleSwitcher, get_style_switcher


def _make_provider(content: str = "daily_chat"):
    provider = MagicMock(spec=LLMProvider)
    provider.model = "test-model"
    response = MagicMock()
    response.content = content
    provider.async_chat_completion = AsyncMock(return_value=response)
    return provider


def test_singleton():
    assert get_style_switcher() is get_style_switcher()


@pytest.mark.asyncio
async def test_rule_short_greeting_skips_llm():
    """短问候消息走规则快速路径，不调用 LLM（性能优化核心）。"""
    switcher = StyleSwitcher()
    provider = _make_provider()
    style = await switcher.analyze("你好", provider)
    assert style == "default"
    provider.async_chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_short_comfort_uses_healing():
    switcher = StyleSwitcher()
    provider = _make_provider()
    style = await switcher.analyze("我有点难过", provider)
    assert style == "healing"
    provider.async_chat_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_short_affectionate_uses_sweet():
    switcher = StyleSwitcher()
    provider = _make_provider()
    style = await switcher.analyze("想你", provider)
    assert style == "sweet"


@pytest.mark.asyncio
async def test_long_message_falls_back_to_llm():
    """长消息未命中规则时回退到 LLM 分析。"""
    switcher = StyleSwitcher()
    provider = _make_provider(content="praise")
    style = await switcher.analyze("今天天气真不错我们一起去公园散步吧", provider)
    assert style == "lively"
    provider.async_chat_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_invalid_scene_defaults():
    switcher = StyleSwitcher()
    provider = _make_provider(content="not_a_scene")
    style = await switcher.analyze("这是一段无法用规则判定的长文本消息", provider)
    assert style == "default"


@pytest.mark.asyncio
async def test_provider_none_rule_path():
    """无 provider（控制台模式）时规则路径仍生效，其余走默认。"""
    switcher = StyleSwitcher()
    assert await switcher.analyze("拜拜", None) == "default"
    assert await switcher.analyze("今天天气不错", None) == "default"
