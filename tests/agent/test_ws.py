"""测试 WS 网关：握手、ping/pong、消息分发、事件转发"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.events import (
    RunFinished,
    RunStarted,
    TextMessageDelta,
    TextMessageEnd,
    TextMessageStart,
)
from agent.ws import create_ws_router
from core.llm.models import TokenUsage


class FakeSession:
    """供 WS 测试的假会话：可配置 submit 事件流与 interrupt 记录。"""

    def __init__(self, events=None):
        self._events = events or [RunStarted(session_id="s"), RunFinished(session_id="s")]
        self.interrupted = False
        self.submit_count = 0
        self.service = MagicMock()
        self.service.usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
        self.service.aclose = AsyncMock()
        self.loop = MagicMock()
        self.loop.resolve_confirmation = AsyncMock()

    async def submit(self, _text):
        self.submit_count += 1
        for ev in self._events:
            yield ev

    def interrupt(self):
        self.interrupted = True


def make_client(fake_session):
    async def factory(_conversation_id):
        return fake_session

    app = FastAPI(title="Test Agent")
    app.include_router(create_ws_router(session_factory=factory))
    return TestClient(app)


class TestWsConnection:
    def test_handshake_ok(self):
        client = make_client(FakeSession())
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_stop_calls_interrupt(self):
        session = FakeSession()
        client = make_client(session)
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "stop"})
            # stop 不产生回复，留出事件循环推进时间
            time.sleep(0.05)
            assert session.interrupted is True

    def test_user_message_events_forwarded(self):
        events = [
            RunStarted(session_id="s"),
            TextMessageStart(message_id="m1"),
            TextMessageDelta(message_id="m1", text="你"),
            TextMessageDelta(message_id="m1", text="好"),
            TextMessageEnd(message_id="m1", full_text="你好"),
            RunFinished(session_id="s"),
        ]
        session = FakeSession(events)
        client = make_client(session)
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "user_message", "text": "你好"})
            received = []
            # 事件 + token_usage 共 7 条，收满即止，防止 receive 永久阻塞
            while len(received) < 7:
                received.append(ws.receive_json())
        types = [d["type"] for d in received]
        assert types[0] == "run_started"
        assert "text_message_start" in types
        assert "text_message_content" in types
        assert "text_message_end" in types
        assert "run_finished" in types
        # 每次回复后应有 token_usage
        assert "token_usage" in types

    def test_confirm_response_routes(self):
        session = FakeSession()
        client = make_client(session)
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "confirm_response", "call_id": "c1", "allowed": True})
            time.sleep(0.05)
            session.loop.resolve_confirmation.assert_awaited_once_with("c1", allowed=True)

    def test_empty_user_message_ignored(self):
        """空文本 user_message 不应触发 agent 运行"""
        session = FakeSession()
        client = make_client(session)
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "user_message", "text": "   "})
            ws.send_json({"type": "ping"})
            # 收 pong，确认链路正常
            assert ws.receive_json()["type"] == "pong"
            time.sleep(0.05)
        assert session.submit_count == 0
