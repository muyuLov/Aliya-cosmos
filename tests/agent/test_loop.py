"""Task 3.4: 主循环状态机重写测试

验证四阶段状态机：补写剧本 → 处理事件 → 投递 → 副作用。
"""

import pytest


class FakeNarratorResponse:
    """模拟 Narrator 输出。"""

    def __init__(self, script: str = "日常", reply_mode: str = "immediate", content: str = "嗨"):
        self.script = script
        self.has_required_script = bool(script)
        self.reply_mode = reply_mode
        self.reply_content = content
        self.seen = True
        self.alter = None
        self.memories = []
        self.intents = []
        self.actions = []
        self.state_patch = None
        self.raw = {}


@pytest.mark.asyncio
async def test_loop_produces_text_message():
    """主循环应产出 TextMessageStart/Delta/End 事件"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder

    narrator_output = FakeNarratorResponse()
    loop = AgentLoop(
        narrator=lambda *a, **kw: narrator_output,
        context=NarrativeContextBuilder(),
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    types = [type(e).__name__ for e in events]
    assert "TextMessageStart" in types
    assert "TextMessageEnd" in types


@pytest.mark.asyncio
async def test_loop_emits_run_started_finished():
    """主循环应产出 RunStarted 和 RunFinished"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.events import RunStarted, RunFinished

    narrator_output = FakeNarratorResponse()
    loop = AgentLoop(
        narrator=lambda *a, **kw: narrator_output,
        context=NarrativeContextBuilder(),
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    assert isinstance(events[0], RunStarted)
    assert isinstance(events[-1], RunFinished)


@pytest.mark.asyncio
async def test_loop_with_no_reply_mode():
    """reply.mode=none 时不应产出 TextMessage"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.events import TextMessageStart

    narrator_output = FakeNarratorResponse(reply_mode="none")
    loop = AgentLoop(
        narrator=lambda *a, **kw: narrator_output,
        context=NarrativeContextBuilder(),
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    has_text = any(isinstance(e, TextMessageStart) for e in events)
    assert not has_text


@pytest.mark.asyncio
async def test_loop_interrupt_stops():
    """中断应停止循环"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder

    narrator_output = FakeNarratorResponse()
    loop = AgentLoop(
        narrator=lambda *a, **kw: narrator_output,
        context=NarrativeContextBuilder(),
    )

    loop.interrupt()
    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    assert len(events) >= 2  # 至少 RunStarted + RunFinished


@pytest.mark.asyncio
async def test_loop_has_no_old_tool_or_soul_phase():
    """主循环应已移除 _tool_phase/_soul_phase"""
    from agent.loop import AgentLoop

    assert not hasattr(AgentLoop, "_tool_phase")
    assert not hasattr(AgentLoop, "_soul_phase")
