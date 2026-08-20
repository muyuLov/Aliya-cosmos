"""测试 C2：WeChatEventSink 文本聚合与确认卡片触发。"""

from __future__ import annotations

import pytest

from agent.channels.wechat_sink import WeChatEventSink
from agent.events import CONFIRM_REQUEST, ProtocolEvent, TextMessageDelta, TextMessageEnd


class FakeWeChatClient:
    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_cards: list[tuple[str, dict]] = []

    async def send_text(self, user_id: str, text: str) -> None:
        self.sent_texts.append((user_id, text))

    async def send_card(self, user_id: str, card: dict) -> None:
        self.sent_cards.append((user_id, card))


def make_sink(confirm: bool = True):
    client = FakeWeChatClient()
    return WeChatEventSink(client, "zhangsan", confirm=confirm), client


@pytest.mark.asyncio
async def test_text_deltas_aggregated_into_single_send():
    sink, client = make_sink()
    await sink.emit(TextMessageDelta(message_id="m", text="你"))
    await sink.emit(TextMessageDelta(message_id="m", text="好"))
    await sink.emit(TextMessageEnd(message_id="m", full_text="你好"))
    assert client.sent_texts == [("zhangsan", "你好")]


@pytest.mark.asyncio
async def test_empty_end_does_not_send():
    sink, client = make_sink()
    await sink.emit(TextMessageEnd(message_id="m", full_text=""))
    assert client.sent_texts == []


@pytest.mark.asyncio
async def test_confirm_request_sends_card():
    sink, client = make_sink(confirm=True)
    await sink.emit(
        ProtocolEvent(
            type=CONFIRM_REQUEST,
            payload={"tool": "send_email", "params": {}, "call_id": "call_1"},
        )
    )
    assert len(client.sent_cards) == 1
    user_id, card = client.sent_cards[0]
    assert user_id == "zhangsan"
    assert card["task_id"] == "call_1"
    # 按钮 key 携带 user_id + call_id，供卡片回调解出
    keys = [b["key"] for b in card["button_list"]]
    assert keys == ["allow:zhangsan:call_1", "deny:zhangsan:call_1"]


@pytest.mark.asyncio
async def test_confirm_disabled_ignores_request():
    sink, client = make_sink(confirm=False)
    await sink.emit(
        ProtocolEvent(type=CONFIRM_REQUEST, payload={"tool": "x", "params": {}, "call_id": "c"})
    )
    assert client.sent_cards == []
