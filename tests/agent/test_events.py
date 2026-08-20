"""测试事件流模型：进程内事件、线上协议映射、EventSink 接口"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.events import (
    RunFinished,
    RunStarted,
    StepFinished,
    StepStarted,
    TEXT_MESSAGE_CONTENT,
    TEXT_MESSAGE_END,
    TEXT_MESSAGE_START,
    TextMessageDelta,
    TextMessageEnd,
    TextMessageStart,
    ToolCallStart,
    to_protocol,
)


class TestAgentEvents:
    def test_run_started_fields(self):
        ev = RunStarted(session_id="s1")
        assert ev.session_id == "s1"

    def test_step_started(self):
        ev = StepStarted(phase="tool")
        assert ev.phase == "tool"

    def test_tool_call_start(self):
        ev = ToolCallStart(call_id="c1", tool_name="memory_query", arguments={"query": "x"})
        assert ev.call_id == "c1"
        assert ev.tool_name == "memory_query"
        assert ev.arguments == {"query": "x"}

    def test_frozen_immutable(self):
        ev = TextMessageDelta(message_id="m1", text="hi")
        with pytest.raises(AttributeError):
            setattr(ev, "text", "changed")

    def test_text_message_events(self):
        start = TextMessageStart(message_id="m1")
        delta = TextMessageDelta(message_id="m1", text="你好")
        end = TextMessageEnd(message_id="m1", full_text="你好世界")
        assert start.message_id == "m1"
        assert delta.text == "你好"
        assert end.full_text == "你好世界"


class TestToProtocol:
    def test_text_delta_maps_to_content(self):
        ev = TextMessageDelta(message_id="m1", text="你")
        proto = to_protocol(ev)
        assert proto is not None
        assert proto.type == TEXT_MESSAGE_CONTENT
        assert proto.payload == {"message_id": "m1", "text": "你"}

    def test_text_start_maps(self):
        proto = to_protocol(TextMessageStart(message_id="m1"))
        assert proto is not None
        assert proto.type == TEXT_MESSAGE_START
        assert proto.payload["message_id"] == "m1"

    def test_text_end_maps(self):
        proto = to_protocol(TextMessageEnd(message_id="m1", full_text="hello"))
        assert proto is not None
        assert proto.type == TEXT_MESSAGE_END
        assert proto.payload["full_text"] == "hello"

    def test_tool_call_start_maps(self):
        proto = to_protocol(ToolCallStart(call_id="c1", tool_name="memory_query", arguments={"q": "1"}))
        assert proto is not None
        assert proto.type == "tool_call_start"
        assert proto.payload["tool_name"] == "memory_query"

    def test_run_started_and_finished(self):
        cases = [
            (RunStarted(session_id="s"), "run_started"),
            (RunFinished(session_id="s"), "run_finished"),
            (StepStarted(phase="tool"), "step_started"),
            (StepFinished(phase="tool"), "step_finished"),
        ]
        for ev, expected in cases:
            proto = to_protocol(ev)
            assert proto is not None
            assert proto.type == expected

    def test_unknown_event_returns_none(self):
        from agent.events import AgentEvent

        @dataclass(frozen=True)
        class EmotionChanged(AgentEvent):
            emotion: str

        assert to_protocol(EmotionChanged(emotion="happy")) is None


class TestEventSink:
    def test_duck_typing_implementer(self):
        class Sink:
            async def emit(self, event) -> None:
                self.received = event

        sink = Sink()
        assert hasattr(sink, "emit")
