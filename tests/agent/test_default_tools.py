"""测试内置工具：GetCurrentTimeTool / QueryRecentConversationTool / 默认注册工厂"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llm.models import Message
from agent.context import AgentContext
from agent.config import AgentConfig
from agent.tools import create_default_tool_registry
from agent.tools.get_current_time import GetCurrentTimeTool
from agent.tools.query_recent_conversation import QueryRecentConversationTool


def _make_ctx(**overrides) -> AgentContext:
    kwargs: dict = {
        "conv": MagicMock(),
        "registry": MagicMock(),
        "config": AgentConfig(),
        "prompt_manager": MagicMock(),
        "style_switcher": MagicMock(),
        "brain": MagicMock(),
        "emotion": MagicMock(),
        "cognition": None,
    }
    kwargs.update(overrides)
    return AgentContext(**kwargs)


class TestGetCurrentTimeTool:
    @pytest.mark.asyncio
    async def test_returns_current_time(self):
        ctx = _make_ctx()
        result = await GetCurrentTimeTool().execute({}, ctx)
        assert result.success is True
        data = result.data
        assert "date" in data
        assert "time" in data
        assert "text" in data


class TestQueryRecentConversationTool:
    @pytest.mark.asyncio
    async def test_returns_formatted_history(self):
        conv = MagicMock()
        conv.get_history = AsyncMock(return_value=[
            Message(role="user", content="你好呀"),
            Message(role="assistant", content="嗨，我在呢"),
        ])
        ctx = _make_ctx(conv=conv)
        result = await QueryRecentConversationTool().execute({}, ctx)
        assert result.success is True
        text = result.data["result"]
        assert "用户: 你好呀" in text
        assert "Aliya: 嗨，我在呢" in text

    @pytest.mark.asyncio
    async def test_filters_injected_tool_results(self):
        conv = MagicMock()
        conv.get_history = AsyncMock(return_value=[
            Message(role="user", content="现在几点"),
            Message(
                role="assistant",
                content="[工具执行结果]\n工具 `get_current_time` 执行成功：...",
                metadata={"injected": True, "prefix": "tool_result"},
            ),
            Message(role="assistant", content="现在是下午三点。"),
        ])
        ctx = _make_ctx(conv=conv)
        result = await QueryRecentConversationTool().execute({"limit": 10}, ctx)
        assert result.success is True
        text = result.data["result"]
        assert "工具执行结果" not in text
        assert "现在是下午三点" in text

    @pytest.mark.asyncio
    async def test_limit_clamped_to_max(self):
        conv = MagicMock()
        conv.get_history = AsyncMock(return_value=[
            Message(role="user", content=f"msg{i}") for i in range(30)
        ])
        ctx = _make_ctx(conv=conv)
        result = await QueryRecentConversationTool().execute({"limit": 999}, ctx)
        assert result.success is True
        lines = result.data["result"].split("\n")
        assert len(lines) == 20  # 上限 20

    @pytest.mark.asyncio
    async def test_empty_history(self):
        conv = MagicMock()
        conv.get_history = AsyncMock(return_value=[])
        ctx = _make_ctx(conv=conv)
        result = await QueryRecentConversationTool().execute({}, ctx)
        assert result.success is True
        assert "暂无对话记录" in result.data["result"]

    @pytest.mark.asyncio
    async def test_history_error(self):
        conv = MagicMock()
        conv.get_history = AsyncMock(side_effect=RuntimeError("boom"))
        ctx = _make_ctx(conv=conv)
        result = await QueryRecentConversationTool().execute({}, ctx)
        assert result.success is False
        assert "对话历史不可用" in result.error


class TestDefaultToolRegistry:
    def test_registers_builtin_tools(self):
        registry = create_default_tool_registry()
        names = [t.name for t in registry.list()]
        assert "memory_query" in names
        assert "get_current_time" in names
        assert "query_recent_conversation" in names

    def test_descriptions_include_all_tools(self):
        registry = create_default_tool_registry()
        desc = registry.format_descriptions()
        assert "memory_query" in desc
        assert "get_current_time" in desc
        assert "query_recent_conversation" in desc
