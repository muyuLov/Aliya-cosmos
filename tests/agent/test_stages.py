"""测试阶段模块：assemble / think / soul 独立逻辑"""

# pyright: reportAttributeAccessIssue=false, reportFunctionMemberAccess=false

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import BrainResult
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.tools.base import ToolResult
from agent.stages.assemble import assemble_tool_phase
from agent.stages.think import run_tool_loop
from agent.stages.soul import run_soul_phase
from agent.pipeline import TurnState


def _make_ctx(**_overrides) -> AgentContext:
    brain = MagicMock()
    brain.think = AsyncMock(return_value=BrainResult(reply="", tool_calls=[]))
    brain.think_with_context = AsyncMock(return_value=BrainResult(reply="最终回复", tool_calls=[]))
    brain.compress_conversation = AsyncMock()
    brain.compressed_context = ""
    emotion = MagicMock()
    conv = MagicMock()
    conv.set_system_prompt = AsyncMock()
    conv.set_context_injection = AsyncMock()
    conv.set_emotion_patch = AsyncMock()
    conv.append_message = AsyncMock()
    conv.discard_messages = AsyncMock()
    registry = MagicMock()
    registry.dispatch_all = AsyncMock(return_value=[("memory_query", ToolResult(success=True, data="hi"))])
    registry.format_tool_summary = MagicMock(return_value="工具结果摘要")
    registry.list = MagicMock(return_value=["memory_query"])
    pm = MagicMock()
    pm.build_tool_system_prompt = MagicMock(return_value="tool-system")
    pm.build_soul_system_prompt = MagicMock(return_value="soul-system")
    pm.build_emotion_patch = MagicMock(return_value="emotion-patch")
    notify = AsyncMock()
    return AgentContext(
        conv=conv,
        registry=registry,
        config=AgentConfig(),
        prompt_manager=pm,
        brain=brain,
        emotion=emotion,
        cognition=None,
        memory_manager=None,
        notify=notify,
    )


@pytest.mark.asyncio
async def test_assemble_sets_tool_prompt_and_injection():
    ctx = _make_ctx()
    await assemble_tool_phase(ctx)
    ctx.conv.set_system_prompt.assert_awaited_once_with("tool-system")
    ctx.conv.set_context_injection.assert_awaited_once()


@pytest.mark.asyncio
async def test_assemble_injects_tool_descriptions():
    """工具描述（registry.format_descriptions）应传入工具阶段 system prompt"""
    ctx = _make_ctx()
    ctx.registry.format_descriptions = MagicMock(return_value="### memory_query 描述")
    ctx.prompt_manager.build_tool_system_prompt = MagicMock(return_value="tool-system")
    await assemble_tool_phase(ctx)
    ctx.prompt_manager.build_tool_system_prompt.assert_called_once_with("### memory_query 描述")


@pytest.mark.asyncio
async def test_think_loop_calls_tools_and_injects_result():
    ctx = _make_ctx()
    # 第一轮 think 返回工具调用，第二轮 think_with_context 返回最终回复
    results = [
        BrainResult(reply="", tool_calls=[{"name": "memory_query", "params": {"query": "hi"}}]),
        BrainResult(reply="最终回复", tool_calls=[]),
    ]
    ctx.brain.think = AsyncMock(side_effect=results)
    state = TurnState()
    reply = await run_tool_loop(ctx, "你好", state, notify=ctx.notify)
    assert reply == "最终回复"
    assert state.has_called_tools is True
    assert state.turn == 1
    ctx.registry.dispatch_all.assert_awaited_once()
    ctx.conv.append_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_soul_phase_switches_context_and_replies():
    ctx = _make_ctx()
    ctx.brain.generate_soul_reply = AsyncMock(return_value="灵魂回复")
    reply = await run_soul_phase(ctx)
    assert reply == "灵魂回复"
    ctx.conv.set_system_prompt.assert_awaited_once_with("soul-system")
    ctx.conv.set_context_injection.assert_awaited_once()
