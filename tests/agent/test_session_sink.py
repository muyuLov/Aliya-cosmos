"""测试：AgentSession EventSink 注册与事件广播。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.events import RunFinished, RunStarted, TextMessageDelta
from agent.session import AgentSession


class FakeSink:
    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on

    async def emit(self, event):
        if self.fail_on is not None and isinstance(event, self.fail_on):
            raise RuntimeError("sink boom")
        self.events.append(event)


def make_session(*sinks):
    loop = MagicMock()
    stream = [
        RunStarted(session_id="s"),
        TextMessageDelta(message_id="m", text="你"),
        RunFinished(session_id="s"),
    ]

    async def fake_submit(_text):
        for ev in stream:
            yield ev

    loop.submit_user_message = fake_submit
    service = MagicMock()
    session = AgentSession("s", service, loop)
    for sink in sinks:
        session.add_sink(sink)
    return session, stream


@pytest.mark.asyncio
async def test_sink_receives_all_events():
    sink = FakeSink()
    session, stream = make_session(sink)
    collected = [ev async for ev in session.submit("hi")]
    assert sink.events == stream
    assert collected == stream


@pytest.mark.asyncio
async def test_sink_failure_isolated():
    """sink 抛异常不阻断 submit：事件仍完整 yield，其余 sink 继续广播。"""
    sink = FakeSink(fail_on=RunStarted)
    session, stream = make_session(sink)
    collected = [ev async for ev in session.submit("hi")]
    assert collected == stream
    assert sink.events == stream[1:]  # 首个事件失败被隔离


@pytest.mark.asyncio
async def test_remove_sink_stops_broadcast():
    sink = FakeSink()
    session, stream = make_session(sink)
    session.remove_sink(sink)
    collected = [ev async for ev in session.submit("hi")]
    assert sink.events == []
    assert collected == stream
