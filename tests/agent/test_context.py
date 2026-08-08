"""测试 AgentContext：统一依赖容器可直接作为工具执行上下文"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.context import AgentContext
from agent.config import AgentConfig
from agent.tools.base import ToolResult


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


def test_context_carries_all_dependencies():
    """容器一次收拢全部依赖，工具执行时可取用。"""
    tts = MagicMock()
    player = MagicMock()
    mem = MagicMock()
    confirm = MagicMock()
    perm = MagicMock()
    ctx = _make_ctx(
        memory_manager=mem,
        tts_service=tts,
        audio_player=player,
        confirm_callback=confirm,
        permission_config=perm,
    )
    assert ctx.memory_manager is mem
    assert ctx.tts_service is tts
    assert ctx.audio_player is player
    assert ctx.confirm_callback is confirm
    assert ctx.permission_config is perm


@pytest.mark.asyncio
async def test_context_passed_directly_to_tool():
    """工具 execute 直接接收 AgentContext，可访问对话服务等能力。"""
    conv = MagicMock()
    conv.get_history = AsyncMock(return_value=[])
    ctx = _make_ctx(conv=conv)

    class DummyTool:
        name = "dummy"

        async def execute(self, params: dict, context):
            _ = params
            history = await context.conv.get_history()
            return ToolResult(success=True, data={"history": history})

    result = await DummyTool().execute({}, ctx)
    assert result.success is True
    conv.get_history.assert_awaited_once()
