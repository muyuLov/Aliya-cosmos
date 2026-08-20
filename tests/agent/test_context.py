"""测试上下文构建器：工具阶段 system / 灵魂阶段 system / 接线注入"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent.context import ContextBuilder, inject_soul_context

PROMPTS_DIR = "data/prompts"


class TestContextBuilder:
    def test_build_tool_system(self):
        builder = ContextBuilder(PROMPTS_DIR)
        text = builder.build_tool_system()
        assert "工具调度" in text
        assert "tool_calls" in text

    def test_build_soul_system_contains_all(self):
        builder = ContextBuilder(PROMPTS_DIR)
        text = builder.build_soul_system(
            memory_text="Kane 喜欢读哲学书",
            emotion_patch="（开心地）",
            tool_summary="查询了记忆",
        )
        assert "Aliya" in text
        assert "Kane 喜欢读哲学书" in text
        assert "开心" in text
        assert "查询了记忆" in text

    def test_build_soul_system_order(self):
        builder = ContextBuilder(PROMPTS_DIR)
        text = builder.build_soul_system(
            memory_text="记忆",
            emotion_patch="情绪",
            tool_summary="摘要",
        )
        mem_pos = text.index("相关记忆")
        emo_pos = text.index("情绪状态")
        tool_pos = text.index("工具结果摘要")
        assert mem_pos < emo_pos < tool_pos

    def test_build_soul_system_without_extras(self):
        builder = ContextBuilder(PROMPTS_DIR)
        text = builder.build_soul_system()
        assert "相关记忆" not in text
        assert "情绪状态" not in text
        assert "工具结果摘要" not in text


class TestInjectSoulContext:
    @pytest.mark.asyncio
    async def test_injects_system_prompt(self):
        service = AsyncMock()
        builder = ContextBuilder(PROMPTS_DIR)
        await inject_soul_context(
            service,
            builder,
            memory_text="记忆内容",
            emotion_patch="情绪内容",
            tool_summary="工具摘要",
        )
        call = service.set_system_prompt.call_args
        assert call is not None
        prompt = call[0][0]
        assert "记忆内容" in prompt
        assert "情绪内容" in prompt
        assert "工具摘要" in prompt
