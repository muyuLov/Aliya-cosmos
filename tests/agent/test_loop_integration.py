"""Task 4.4: 循环集成测试

验证 Alter/Agency/休息窗口注入主叙事上下文与受限行动门控。
"""

import json
import pytest


@pytest.mark.asyncio
async def test_loop_injects_alter_into_context():
    """主循环应将 Alter 状态注入上下文"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.emotion.alter import AlterState

    class FakeNarrator:
        def __init__(self):
            self.captured_context = None

        async def invoke(self, system_prompt, context_json, **kw):
            self.captured_context = context_json
            from agent.metadata_parser import NarrativeOutput
            return NarrativeOutput(
                script="测试", has_required_script=True,
                reply_mode="immediate", reply_content="嗨",
            )

    narrator = FakeNarrator()
    alter = AlterState()
    alter.apply(3, "warm")

    loop = AgentLoop(
        narrator=narrator,
        context=NarrativeContextBuilder(),
        alter_state=alter,
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    # 上下文应包含 alter 信息
    assert narrator.captured_context is not None
    assert "alter" in narrator.captured_context
    assert narrator.captured_context["alter"]["direction"] == "warm"


@pytest.mark.asyncio
async def test_loop_injects_agency_into_context():
    """主循环应将 Agency 状态注入上下文"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.proactive.agency import AgencyWindow

    class FakeNarrator:
        def __init__(self):
            self.captured_context = None

        async def invoke(self, system_prompt, context_json, **kw):
            self.captured_context = context_json
            from agent.metadata_parser import NarrativeOutput
            return NarrativeOutput(
                script="测试", has_required_script=True,
                reply_mode="immediate", reply_content="嗨",
            )

    narrator = FakeNarrator()
    agency = AgencyWindow(activity_load=0.3, privacy=True, device_access=True)

    loop = AgentLoop(
        narrator=narrator,
        context=NarrativeContextBuilder(),
        agency_window=agency,
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    assert narrator.captured_context is not None
    assert "agency" in narrator.captured_context
    assert narrator.captured_context["agency"]["capacity"] > 0


@pytest.mark.asyncio
async def test_loop_turn_metadata_from_narrator():
    """主循环应从 narrator 输出产出 TurnMetadata"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.events import TurnMetadata

    from agent.metadata_parser import NarrativeOutput

    output = NarrativeOutput(
        script="测试", has_required_script=True,
        reply_mode="immediate", reply_content="嗨",
        memories=[{"content": "记忆1", "importance": 0.7, "participantId": "user", "kind": "fact"}],
        intents=[{"type": "delay", "summary": "意图1", "notBefore": "", "participantId": "user"}],
    )

    loop = AgentLoop(
        narrator=lambda *a, **kw: output,
        context=NarrativeContextBuilder(),
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    has_turn_metadata = any(isinstance(e, TurnMetadata) for e in events)
    assert has_turn_metadata


@pytest.mark.asyncio
async def test_loop_alter_triggered_event():
    """主循环应从 narrator 输出产出 AlterTriggered"""
    from agent.loop import AgentLoop
    from agent.context import NarrativeContextBuilder
    from agent.events import AlterTriggered

    from agent.metadata_parser import NarrativeOutput

    output = NarrativeOutput(
        script="测试", has_required_script=True,
        reply_mode="immediate", reply_content="嗨",
        alter=3,
    )

    loop = AgentLoop(
        narrator=lambda *a, **kw: output,
        context=NarrativeContextBuilder(),
    )

    events = []
    async for event in loop.submit_user_message("你好"):
        events.append(event)

    has_alter = any(isinstance(e, AlterTriggered) for e in events)
    assert has_alter
