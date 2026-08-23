"""事件流模型：进程内事件（AgentEvent）与线上协议事件（ProtocolEvent）双层设计

参考 Cyrene-Agent 的 "中性 TwoPhaseEvent + AG-UI BaseEvent 包装" 双层模型：
- 进程内事件（AgentEvent）：丰富，供本地副作用（记忆/情绪/TTS/日志）订阅
- 线上协议事件（ProtocolEvent）：精简，供渠道（飞书 / 微信）等外部消费者使用
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


# 事件类型常量（供渠道等外部消费者使用）
CONFIRM_REQUEST = "confirm_request"
ERROR = "error"
NOTICE = "notice"
STATUS_CHANGED = "status_changed"
EMOTION_CHANGED = "emotion_changed"
TTS_FEATURES = "tts_features"


# ── EventSink 接口 ──────────────────────────────────────────────────────────

class EventSink(Protocol):
    """事件订阅接口：任何实现者都可订阅进程内事件

    广播方会同时派发 AgentEvent 与 ProtocolEvent（后者含 CONFIRM_REQUEST），
    实现方自行挑选关心的类型。
    """

    async def emit(self, event: AgentEvent | ProtocolEvent) -> None: ...
