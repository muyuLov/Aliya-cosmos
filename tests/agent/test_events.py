"""Task 3.2: 事件模型与协议整体重构测试

验证新事件类型（TurnMetadata / AlterTriggered 等）与 to_protocol 映射。
"""

import pytest
from agent.events import (
    AgentEvent,
    ProtocolEvent,
    RunStarted,
    RunFinished,
    TextMessageStart,
    TextMessageDelta,
    TextMessageEnd,
    TurnMetadata,
    AlterTriggered,
    ProactiveContact,
    SceneClosed,
    AgencyDecision,
    to_protocol,
    EventSink,
    RUN_STARTED,
    RUN_FINISHED,
    TEXT_MESSAGE_START,
    TEXT_MESSAGE_CONTENT,
    TEXT_MESSAGE_END,
    TURN_METADATA,
    ALTER_TRIGGERED,
    PROACTIVE_CONTACT,
    SCENE_CLOSED,
    AGENCY_DECISION,
)


def test_turn_metadata_type():
    """TurnMetadata 应包含 emotion_delta / memory_candidates / state_patches / follow_up_intents"""
    event = TurnMetadata(
        emotion_delta=2,
        memory_candidates=[{"content": "记忆"}],
        state_patches=[],
        follow_up_intents=[],
    )
    assert event.emotion_delta == 2
    assert len(event.memory_candidates) == 1


def test_alter_triggered_type():
    """AlterTriggered 应包含 direction / description / intensity"""
    event = AlterTriggered(
        direction="warm",
        description="温暖的氛围",
        intensity=0.8,
    )
    assert event.direction == "warm"
    assert event.intensity == 0.8


def test_proactive_contact_type():
    """ProactiveContact 应包含 participant_id / reason"""
    event = ProactiveContact(
        participant_id="aliya",
        reason="长时间未联系",
    )
    assert event.participant_id == "aliya"


def test_scene_closed_type():
    """SceneClosed 应包含 scene_id / summary"""
    event = SceneClosed(scene_id="scene_1", summary="对话结束")
    assert event.scene_id == "scene_1"
    assert event.summary == "对话结束"


def test_agency_decision_type():
    """AgencyDecision 应包含 allowed / reason"""
    event = AgencyDecision(allowed=True, reason="符合主体性")
    assert event.allowed is True


def test_to_protocol_run_started():
    """RunStarted 映射为 RUN_STARTED 协议事件"""
    event = RunStarted(session_id="s1")
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == RUN_STARTED
    assert proto.payload["session_id"] == "s1"


def test_to_protocol_run_finished():
    """RunFinished 映射为 RUN_FINISHED 协议事件"""
    event = RunFinished(session_id="s1")
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == RUN_FINISHED


def test_to_protocol_text_message():
    """TextMessage 事件应映射为对应协议事件"""
    start = to_protocol(TextMessageStart(message_id="m1"))
    assert start is not None
    assert start.type == TEXT_MESSAGE_START

    delta = to_protocol(TextMessageDelta(message_id="m1", text="你好"))
    assert delta is not None
    assert delta.type == TEXT_MESSAGE_CONTENT
    assert delta.payload["text"] == "你好"

    end = to_protocol(TextMessageEnd(message_id="m1", full_text="你好"))
    assert end is not None
    assert end.type == TEXT_MESSAGE_END
    assert end.payload["full_text"] == "你好"


def test_to_protocol_turn_metadata():
    """TurnMetadata 应映射为 TURN_METADATA 协议事件"""
    event = TurnMetadata(
        emotion_delta=1,
        memory_candidates=[],
        state_patches=[],
        follow_up_intents=[],
    )
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == TURN_METADATA
    assert proto.payload["emotion_delta"] == 1


def test_to_protocol_alter_triggered():
    """AlterTriggered 应映射为 ALTER_TRIGGERED 协议事件"""
    event = AlterTriggered(direction="cool", description="冷淡", intensity=0.5)
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == ALTER_TRIGGERED


def test_to_protocol_proactive_contact():
    """ProactiveContact 应映射为 PROACTIVE_CONTACT 协议事件"""
    event = ProactiveContact(participant_id="aliya", reason="无聊")
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == PROACTIVE_CONTACT


def test_to_protocol_scene_closed():
    """SceneClosed 应映射为 SCENE_CLOSED 协议事件"""
    event = SceneClosed(scene_id="s1", summary="结束了")
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == SCENE_CLOSED


def test_to_protocol_agency_decision():
    """AgencyDecision 应映射为 AGENCY_DECISION 协议事件"""
    event = AgencyDecision(allowed=False, reason="隐私约束")
    proto = to_protocol(event)
    assert proto is not None
    assert proto.type == AGENCY_DECISION
    assert proto.payload["allowed"] is False


def test_old_step_types_deprecated():
    """旧 StepStarted/StepFinished/ToolCall* 事件类型仍存在但标记为向后兼容（loop 重写后移除）"""
    import agent.events as mod

    assert hasattr(mod, "StepStarted")
    assert hasattr(mod, "StepFinished")
    assert hasattr(mod, "ToolCallStart")
    assert hasattr(mod, "ToolCallResult")
    assert hasattr(mod, "ToolCallEnd")
