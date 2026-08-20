"""测试 C2：企业微信 webhook 路由——消息驱动、URL 校验、确认回调。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.channels.wechat_router import create_wechat_router
from agent.events import RunFinished, RunStarted, TextMessageEnd


class FakeWeChatClient:
    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []

    async def send_text(self, user_id: str, text: str) -> None:
        self.sent_texts.append((user_id, text))

    async def send_card(self, _user_id: str, _card: dict) -> None:
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


def make_client(session: FakeSession, client: FakeWeChatClient | None = None, confirm: bool = True, mocker: Any = None):
    client = client or FakeWeChatClient()

    async def fake_build(_cid):
        return session

    import agent.channels.wechat_router as wechat_router
    mocker.patch.object(wechat_router, "build_agent_session", new=fake_build)
    app = FastAPI(title="WeChat Test")
    app.include_router(create_wechat_router(client, confirm=confirm))
    return TestClient(app), client


def _message_body(user_id: str = "zhangsan", text: str = "hi") -> dict:
    return {
        "ToUserName": "Aliya",
        "FromUserName": user_id,
        "CreateTime": 1700000000,
        "MsgType": "text",
        "Content": text,
        "MsgId": "1001",
    }


def test_verify_echostr(mocker):
    client_, _ = make_client(FakeSession(), mocker=mocker)
    resp = client_.get("/channels/wechat?echostr=abc123")
    assert resp.status_code == 200
    assert resp.text == "abc123"


def test_message_drives_session_and_reply(mocker):
    session = FakeSession()
    client_, fake = make_client(session, mocker=mocker)
    resp = client_.post("/channels/wechat", json=_message_body(text="你好"))
    assert resp.status_code == 200
    assert resp.text == "success"
    # sink 已绑定到会话，完整回复发送到企业微信
    assert fake.sent_texts == [("zhangsan", "你好，我是 Aliya")]
    assert len(session.sinks) == 1


def test_empty_message_safe(mocker):
    session = FakeSession()
    client_, fake = make_client(session, mocker=mocker)
    resp = client_.post("/channels/wechat", json=_message_body(text="  "))
    assert resp.text == "success"
    assert fake.sent_texts == []


def test_card_callback_resolves_confirmation(mocker):
    session = FakeSession()
    client_, _ = make_client(session, mocker=mocker)
    # 先发消息建立会话（sink 绑定），再模拟用户点击确认
    client_.post("/channels/wechat", json=_message_body(text="hi"))
    resp = client_.post(
        "/channels/wechat/card",
        json={"call_id": "call_1", "user_id": "zhangsan", "allowed": True},
    )
    assert resp.status_code == 200
    session.loop.resolve_confirmation.assert_awaited_once_with("call_1", allowed=True)


def test_card_callback_missing_session_safe(mocker):
    client_, _ = make_client(FakeSession(), mocker=mocker)
    resp = client_.post(
        "/channels/wechat/card",
        json={"call_id": "call_1", "user_id": "no_such_user", "allowed": True},
    )
    assert resp.json() == {"code": 0}
