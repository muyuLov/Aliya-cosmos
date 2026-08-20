"""测试 AgentSession 与会话装配"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.events import RunStarted
from agent.session import AgentSession, build_session_factory


def make_session(conversation_id="s1"):
    loop = MagicMock()
    loop.interrupt_called = False
    loop.reset_abort_called = False

    async def fake_submit(_text):
        yield RunStarted(session_id=conversation_id)

    def fake_interrupt():
        loop.interrupt_called = True

    def fake_reset_abort():
        loop.reset_abort_called = True

    loop.submit_user_message = fake_submit
    loop.interrupt = fake_interrupt
    loop.reset_abort = fake_reset_abort
    service = MagicMock()
    session = AgentSession(conversation_id, service, loop)
    return session


class TestAgentSession:
    @pytest.mark.asyncio
    async def test_submit_forwards_events(self):
        session = make_session()
        events = [ev async for ev in session.submit("hi")]
        assert len(events) == 1
        assert isinstance(events[0], RunStarted)

    def test_interrupt_forwards(self):
        session = make_session()
        session.interrupt()
        assert getattr(session.loop, "interrupt_called") is True

    def test_reset_abort_forwards(self):
        session = make_session()
        session.reset_abort()
        assert getattr(session.loop, "reset_abort_called") is True

    def test_properties(self):
        session = make_session()
        assert session.conversation_id == "s1"
        assert session.service is session._service
        assert session.loop is session._loop


class TestSessionFactory:
    def test_build_session_factory_returns_callable(self):
        factory = build_session_factory()
        assert callable(factory)

