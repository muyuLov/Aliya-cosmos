"""测试 B4：飞书 webhook 路由——消息驱动、challenge 校验、卡片回调。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.channels.feishu_router import create_feishu_router
from agent.events import RunFinished, RunStarted, TextMessageEnd


class FakeFeishuClient:
    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []

    async def send_text(self, chat_id: str, text: str) -> None:
        self.sent_texts.append((chat_id, text))

    async def send_card(self, _chat_id: str, _card: dict) -> None:
        pass


class FakeSession:
    def __init__(self):
        self.sinks = []
        self.loop = MagicMock()
        self.loop.resolve_confirmation = AsyncMock()

    def add_sink(self, sink) -> None:
        self.sinks.append(sink)

    async def submit(self, _text):
        # 模拟 AgentSession.submit：事件广播给所有 sink 后再 yield
        events = [
            RunStarted(session_id="s"),
            TextMessageEnd(message_id="m", full_text="你好，我是 Aliya"),
            RunFinished(session_id="s"),
        ]
        for ev in events:
            for sink in self.sinks:
                await sink.emit(ev)
            yield ev


def make_client(session: FakeSession, client: FakeFeishuClient | None = None, confirm: bool = True, mocker: Any = None):
    client = client or FakeFeishuClient()

    async def fake_build(_cid):
        return session

    import agent.channels.feishu_router as feishu_router
    mocker.patch.object(feishu_router, "build_agent_session", new=fake_build)
    app = FastAPI(title="Feishu Test")
    app.include_router(create_feishu_router(client, confirm=confirm))
    return TestClient(app), client


def _message_body(chat_id: str = "oc_1", text: str = "hi") -> dict:
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "message": {
                "message_id": "om_1",
                "chat_id": chat_id,
                "content": json.dumps({"text": text}),
            }
        },
    }


def test_url_verification_challenge(mocker):
    client_, _ = make_client(FakeSession(), mocker=mocker)
    resp = client_.post("/channels/feishu", json={"type": "url_verification", "challenge": "xyz"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "xyz"}


def test_message_drives_session_and_reply(mocker):
    session = FakeSession()
    client_, fake = make_client(session, mocker=mocker)
    resp = client_.post("/channels/feishu", json=_message_body(text="你好"))
    assert resp.status_code == 200
    assert resp.json() == {"code": 0}
    # sink 已绑定到会话，完整回复发送到飞书
    assert fake.sent_texts == [("oc_1", "你好，我是 Aliya")]
    assert len(session.sinks) == 1


def test_empty_message_safe(mocker):
    session = FakeSession()
    client_, fake = make_client(session, mocker=mocker)
    resp = client_.post("/channels/feishu", json=_message_body(text="  "))
    assert resp.json() == {"code": 0}
    assert fake.sent_texts == []


def test_card_callback_resolves_confirmation(mocker):
    session = FakeSession()
    client_, _ = make_client(session, mocker=mocker)
    # 先发消息建立会话（sink 绑定），再模拟用户点击确认卡片
    client_.post("/channels/feishu", json=_message_body(text="hi"))
    body = {
        "schema": "2.0",
        "action": {
            "tag": "button",
            "value": {"call_id": "call_1", "chat_id": "oc_1", "allowed": True},
        },
    }
    resp = client_.post("/channels/feishu/card", json=body)
    assert resp.status_code == 200
    session.loop.resolve_confirmation.assert_awaited_once_with("call_1", allowed=True)


def test_card_callback_missing_session_safe(mocker):
    client_, _ = make_client(FakeSession(), mocker=mocker)
    body = {
        "action": {"value": {"call_id": "call_1", "chat_id": "no_such_chat", "allowed": True}}
    }
    resp = client_.post("/channels/feishu/card", json=body)
    assert resp.json() == {"code": 0}
