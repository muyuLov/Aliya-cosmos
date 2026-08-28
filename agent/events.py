"""事件流模型（重构版）：进程内事件（AgentEvent）与线上协议事件（ProtocolEvent）

新事件类型承载主叙事副产物（TurnMetadata / AlterTriggered / ProactiveContact 等），
移除旧两阶段残留事件（StepStarted / StepFinished / ToolCall*）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# ── 进程内事件 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentEvent:
    """进程内事件基类"""


@dataclass(frozen=True)
class RunStarted(AgentEvent):
    session_id: str


@dataclass(frozen=True)
class TextMessageStart(AgentEvent):
    message_id: str


@dataclass(frozen=True)
class TextMessageDelta(AgentEvent):
    message_id: str
    text: str


@dataclass(frozen=True)
class TextMessageEnd(AgentEvent):
    message_id: str
    full_text: str


@dataclass(frozen=True)
class RunFinished(AgentEvent):
    session_id: str


@dataclass(frozen=True)
class TurnMetadata(AgentEvent):
    """单回合副产物：情绪/记忆/补丁/意图"""
    emotion_delta: int = 0
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)
    state_patches: list[dict[str, Any]] = field(default_factory=list)
    follow_up_intents: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AlterTriggered(AgentEvent):
    """Alter 氛围偏移触发"""
    direction: str = ""
    description: str = ""
    intensity: float = 0.0


@dataclass(frozen=True)
class ProactiveContact(AgentEvent):
    """主动联系触发"""
    participant_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SceneClosed(AgentEvent):
    """场景关闭"""
    scene_id: str = ""
    summary: str = ""


@dataclass(frozen=True)
class AgencyDecision(AgentEvent):
    """主体约束裁决"""
    allowed: bool = False
    reason: str = ""


# ── 线上协议事件 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProtocolEvent:
    type: str
    payload: dict[str, Any]


# 事件类型常量
RUN_STARTED = "run_started"
TEXT_MESSAGE_START = "text_message_start"
TEXT_MESSAGE_CONTENT = "text_message_content"
TEXT_MESSAGE_END = "text_message_end"
RUN_FINISHED = "run_finished"
TURN_METADATA = "turn_metadata"
ALTER_TRIGGERED = "alter_triggered"
PROACTIVE_CONTACT = "proactive_contact"
SCENE_CLOSED = "scene_closed"
AGENCY_DECISION = "agency_decision"
CONFIRM_REQUEST = "confirm_request"
ERROR = "error"
NOTICE = "notice"
TOKEN_USAGE = "token_usage"


# ── 旧事件类型向后兼容别名（loop.py 重写后移除）─────────────────────

@dataclass(frozen=True)
class StepStarted(AgentEvent):
    phase: str = ""


@dataclass(frozen=True)
class StepFinished(AgentEvent):
    phase: str = ""


@dataclass(frozen=True)
class ToolCallStart(AgentEvent):
    call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallResult(AgentEvent):
    call_id: str = ""
    output: str = ""


@dataclass(frozen=True)
class ToolCallEnd(AgentEvent):
    call_id: str = ""


def to_protocol(event: AgentEvent, **_ctx) -> ProtocolEvent | None:
    """将进程内事件映射为线上协议事件；不进入线上协议的内部事件返回 None。"""
    if isinstance(event, RunStarted):
        return ProtocolEvent(RUN_STARTED, {"session_id": event.session_id})
    if isinstance(event, RunFinished):
        return ProtocolEvent(RUN_FINISHED, {"session_id": event.session_id})
    if isinstance(event, TextMessageStart):
        return ProtocolEvent(TEXT_MESSAGE_START, {"message_id": event.message_id})
    if isinstance(event, TextMessageDelta):
        return ProtocolEvent(TEXT_MESSAGE_CONTENT, {"message_id": event.message_id, "text": event.text})
    if isinstance(event, TextMessageEnd):
        return ProtocolEvent(TEXT_MESSAGE_END, {"message_id": event.message_id, "full_text": event.full_text})
    if isinstance(event, TurnMetadata):
        return ProtocolEvent(TURN_METADATA, {
            "emotion_delta": event.emotion_delta,
            "memory_candidates": event.memory_candidates,
            "state_patches": event.state_patches,
            "follow_up_intents": event.follow_up_intents,
        })
    if isinstance(event, AlterTriggered):
        return ProtocolEvent(ALTER_TRIGGERED, {
            "direction": event.direction,
            "description": event.description,
            "intensity": event.intensity,
        })
    if isinstance(event, ProactiveContact):
        return ProtocolEvent(PROACTIVE_CONTACT, {
            "participant_id": event.participant_id,
            "reason": event.reason,
        })
    if isinstance(event, SceneClosed):
        return ProtocolEvent(SCENE_CLOSED, {
            "scene_id": event.scene_id,
            "summary": event.summary,
        })
    if isinstance(event, AgencyDecision):
        return ProtocolEvent(AGENCY_DECISION, {
            "allowed": event.allowed,
            "reason": event.reason,
        })
    return None


# ── EventSink 接口 ──────────────────────────────────────────────────────────

class EventSink(Protocol):
    """事件订阅接口：任何实现者都可订阅进程内事件

    广播方同时派发 AgentEvent 与 ProtocolEvent（后者含 CONFIRM_REQUEST），
    实现方自行挑选关心的类型。
    """

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None: ...
