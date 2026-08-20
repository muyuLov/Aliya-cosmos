"""事件流模型：进程内事件（AgentEvent）与线上协议事件（ProtocolEvent）双层设计

参考 Cyrene-Agent 的 "中性 TwoPhaseEvent + AG-UI BaseEvent 包装" 双层模型：
- 进程内事件（AgentEvent）：丰富，供本地副作用（记忆/情绪/TTS/日志）订阅
- 线上协议事件（ProtocolEvent）：精简，映射后转发给 WS 网关 / GUI / 渠道
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ── 进程内事件 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentEvent:
    """进程内事件基类"""


@dataclass(frozen=True)
class RunStarted(AgentEvent):
    session_id: str


@dataclass(frozen=True)
class StepStarted(AgentEvent):
    phase: str                 # "tool" | "soul"


@dataclass(frozen=True)
class StepFinished(AgentEvent):
    phase: str


@dataclass(frozen=True)
class ToolCallStart(AgentEvent):
    call_id: str
    tool_name: str
    arguments: dict


@dataclass(frozen=True)
class ToolCallResult(AgentEvent):
    call_id: str
    output: str


@dataclass(frozen=True)
class ToolCallEnd(AgentEvent):
    call_id: str


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


# ── 线上协议事件 ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProtocolEvent:
    type: str
    payload: dict[str, Any]


# 事件类型常量
RUN_STARTED = "run_started"
STEP_STARTED = "step_started"
STEP_FINISHED = "step_finished"
TOOL_CALL_START = "tool_call_start"
TOOL_CALL_RESULT = "tool_call_result"
TOOL_CALL_END = "tool_call_end"
TEXT_MESSAGE_START = "text_message_start"
TEXT_MESSAGE_CONTENT = "text_message_content"
TEXT_MESSAGE_END = "text_message_end"
RUN_FINISHED = "run_finished"
CONFIRM_REQUEST = "confirm_request"
ERROR = "error"
NOTICE = "notice"
TOKEN_USAGE = "token_usage"
STATUS_CHANGED = "status_changed"
EMOTION_CHANGED = "emotion_changed"
TTS_FEATURES = "tts_features"


def to_protocol(event: AgentEvent, **_ctx) -> ProtocolEvent | None:
    """将进程内事件映射为线上协议事件；不进入线上协议的内部事件返回 None。"""
    if isinstance(event, RunStarted):
        return ProtocolEvent(RUN_STARTED, {"session_id": event.session_id})
    if isinstance(event, RunFinished):
        return ProtocolEvent(RUN_FINISHED, {"session_id": event.session_id})
    if isinstance(event, StepStarted):
        return ProtocolEvent(STEP_STARTED, {"phase": event.phase})
    if isinstance(event, StepFinished):
        return ProtocolEvent(STEP_FINISHED, {"phase": event.phase})
    if isinstance(event, ToolCallStart):
        return ProtocolEvent(TOOL_CALL_START, {"tool_name": event.tool_name, "arguments": event.arguments})
    if isinstance(event, ToolCallResult):
        return ProtocolEvent(TOOL_CALL_RESULT, {"call_id": event.call_id, "output": event.output})
    if isinstance(event, ToolCallEnd):
        return ProtocolEvent(TOOL_CALL_END, {"call_id": event.call_id})
    if isinstance(event, TextMessageStart):
        return ProtocolEvent(TEXT_MESSAGE_START, {"message_id": event.message_id})
    if isinstance(event, TextMessageDelta):
        return ProtocolEvent(TEXT_MESSAGE_CONTENT, {"message_id": event.message_id, "text": event.text})
    if isinstance(event, TextMessageEnd):
        return ProtocolEvent(TEXT_MESSAGE_END, {"message_id": event.message_id, "full_text": event.full_text})
    return None


# ── EventSink 接口 ──────────────────────────────────────────────────────────

class EventSink(Protocol):
    """事件订阅接口：任何实现者都可订阅进程内事件

    广播方会同时派发 AgentEvent 与 ProtocolEvent（后者含 CONFIRM_REQUEST），
    实现方自行挑选关心的类型。
    """

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None: ...
