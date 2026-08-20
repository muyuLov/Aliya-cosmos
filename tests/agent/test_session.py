"""测试 AgentSession 与会话管理器"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.events import RunStarted
from agent.session import AgentSession, SessionManager


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


class TestSessionManager:
    def test_get_or_create_same_instance(self):
        mgr = SessionManager()
        created = []

        def factory():
            s = make_session("s1")
            created.append(s)
            return s

        a = mgr.get_or_create("s1", factory)
        b = mgr.get_or_create("s1", factory)
        assert a is b
        assert len(created) == 1

    def test_get_and_remove(self):
        mgr = SessionManager()
        session = make_session("s1")
        mgr._sessions["s1"] = session
        assert mgr.get("s1") is session
        mgr.remove("s1")
        assert mgr.get("s1") is None

    def test_remove_missing_no_error(self):
        mgr = SessionManager()
        mgr.remove("nope")  # 不应抛异常

    def test_close_all(self):
        mgr = SessionManager()
        mgr._sessions["s1"] = make_session("s1")
        mgr._sessions["s2"] = make_session("s2")
        mgr.close_all()
        assert mgr.get("s1") is None
        assert mgr.get("s2") is None
