"""测试 AgentPipeline：完整流转 / 钩子触发 / 状态通知 / 错误降级"""

# pyright: reportAttributeAccessIssue=false, reportFunctionMemberAccess=false, reportOptionalMemberAccess=false

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.brain import BrainResult
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.hooks import HookPoint, HookRegistry
from agent.pipeline import AgentPipeline
from agent.tools.base import ToolResult


def _make_ctx(**_overrides) -> AgentContext:
    brain = MagicMock()
    brain.think = AsyncMock(return_value=BrainResult(reply="", tool_calls=[]))
    brain.think_with_context = AsyncMock(return_value=BrainResult(reply="最终回复", tool_calls=[]))
    brain.generate_soul_reply = AsyncMock(return_value="灵魂回复")
    brain.force_summary_reply = AsyncMock(return_value="兜底回复")
    brain.compress_conversation = AsyncMock()
    brain.compressed_context = ""
    brain.cot_enabled = True
    brain.use_native_thinking = False

    emotion = MagicMock()
    emotion.current_emotion = "happy"
    emotion.get_state = MagicMock(return_value={})

    conv = MagicMock()
    conv.set_system_prompt = AsyncMock()
    conv.set_context_injection = AsyncMock()
    conv.set_emotion_patch = AsyncMock()
    conv.append_message = AsyncMock()
    conv.discard_messages = AsyncMock()
    conv.conversation_id = "test-id"

    registry = MagicMock()
    registry.dispatch_all = AsyncMock(return_value=[("memory_query", ToolResult(success=True, data="hi"))])
    registry.format_tool_summary = MagicMock(return_value="summary")
    registry.list = MagicMock(return_value=["memory_query"])

    pm = MagicMock()
    pm.build_tool_system_prompt = MagicMock(return_value="tool-system")
    pm.build_soul_system_prompt = MagicMock(return_value="soul-system")
    pm.build_emotion_patch = MagicMock(return_value="patch")

    return AgentContext(
        conv=conv,
        registry=registry,
        config=AgentConfig(),
        prompt_manager=pm,
        style_switcher=MagicMock(),
        brain=brain,
        emotion=emotion,
        cognition=None,
        memory_manager=None,
        notify=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_handle_message_no_tools_goes_soul_phase():
    ctx = _make_ctx()
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("你好")
    ctx.brain.think.assert_awaited_once_with("你好")
    ctx.brain.generate_soul_reply.assert_awaited_once()
    ctx.conv.set_system_prompt.assert_awaited()
    assert ctx.notify.await_count > 0  # 有通知推送


@pytest.mark.asyncio
async def test_hooks_triggered_in_order():
    ctx = _make_ctx()
    hooks = HookRegistry()
    calls: list[str] = []

    async def bt(_text: str) -> None:
        calls.append("before_turn")

    async def at(_name: str, _result: object) -> None:
        calls.append("after_tool")

    async def atn(_reply: str) -> None:
        calls.append("after_turn")

    hooks.register(HookPoint.BEFORE_TURN, bt)
    hooks.register(HookPoint.AFTER_TOOL, at)
    hooks.register(HookPoint.AFTER_TURN, atn)
    pipe = AgentPipeline(ctx, hooks=hooks)
    await pipe.handle_user_message("你好")
    assert "before_turn" in calls
    assert "after_turn" in calls


@pytest.mark.asyncio
async def test_after_tool_hook_fired_when_tools_called():
    """工具调用后 AFTER_TOOL 钩子点被触发（认知学习接线）。"""
    ctx = _make_ctx()
    ctx.brain.think = AsyncMock(
        side_effect=[
            BrainResult(reply="", tool_calls=[{"name": "memory_query", "params": {"query": "hi"}}]),
            BrainResult(reply="", tool_calls=[]),
        ]
    )
    ctx.brain.think_with_context = AsyncMock(
        return_value=BrainResult(reply="最终回复", tool_calls=[])
    )
    hooks = HookRegistry()
    observed: list[str] = []

    async def at(name: str, _result: object) -> None:
        observed.append(name)

    hooks.register(HookPoint.AFTER_TOOL, at)
    pipe = AgentPipeline(ctx, hooks=hooks)
    await pipe.handle_user_message("你好")
    assert observed == ["memory_query"]


@pytest.mark.asyncio
async def test_auto_style_switch_updates_single_source():
    """自动风格切换后 current_style 单一来源被更新（I1 回归防护）。"""
    ctx = _make_ctx()
    ctx.config.auto_style_enabled = True
    ctx.style_switcher.analyze = AsyncMock(return_value="warm")
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("你好")
    assert pipe.current_style == "warm"


@pytest.mark.asyncio
async def test_error_falls_back_to_force_summary():
    ctx = _make_ctx()
    ctx.brain.think = AsyncMock(side_effect=RuntimeError("llm down"))
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("你好")
    ctx.brain.force_summary_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_transitions_notified():
    ctx = _make_ctx()
    pipe = AgentPipeline(ctx)
    await pipe.handle_user_message("hi")
    notified_types = [c.args[0]["type"] for c in ctx.notify.call_args_list]
    assert "brain_start" in notified_types
    assert "state_change" in notified_types
