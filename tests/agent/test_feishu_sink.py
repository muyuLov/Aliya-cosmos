"""测试 B3：FeishuEventSink 文本聚合与确认卡片触发。"""

from __future__ import annotations

import pytest

from agent.channels.feishu_sink import FeishuEventSink
from agent.events import CONFIRM_REQUEST, ProtocolEvent, TextMessageDelta, TextMessageEnd


class FakeFeishuClient:
    def __init__(self):
        self.sent_texts: list[tuple[str, str]] = []
        self.sent_cards: list[tuple[str, dict]] = []

    async def send_text(self, chat_id: str, text: str) -> None:
        self.sent_texts.append((chat_id, text))

    async def send_card(self, chat_id: str, card: dict) -> None:
        self.sent_cards.append((chat_id, card))


def make_sink(confirm: bool = True):
    client = FakeFeishuClient()
    return FeishuEventSink(client, "oc_1", confirm=confirm), client


@pytest.mark.asyncio
async def test_text_deltas_aggregated_into_single_send():
    sink, client = make_sink()
    await sink.emit(TextMessageDelta(message_id="m", text="你"))
    await sink.emit(TextMessageDelta(message_id="m", text="好"))
    await sink.emit(TextMessageEnd(message_id="m", full_text="你好"))
    assert client.sent_texts == [("oc_1", "你好")]


@pytest.mark.asyncio
async def test_end_without_deltas_uses_full_text():
    sink, client = make_sink()
    await sink.emit(TextMessageEnd(message_id="m", full_text="完整回复"))
    assert client.sent_texts == [("oc_1", "完整回复")]


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
    chat_id, card = client.sent_cards[0]
    assert chat_id == "oc_1"
    # 按钮 value 携带 call_id / chat_id / allowed，供卡片回调解出
    buttons = card["data"]["template_card"]["action"]["button_list"]
    assert len(buttons) == 2
    assert buttons[0]["value"] == {"call_id": "call_1", "chat_id": "oc_1", "allowed": True}
    assert buttons[1]["value"] == {"call_id": "call_1", "chat_id": "oc_1", "allowed": False}


@pytest.mark.asyncio
async def test_confirm_disabled_ignores_request():
    sink, client = make_sink(confirm=False)
    await sink.emit(
        ProtocolEvent(type=CONFIRM_REQUEST, payload={"tool": "x", "params": {}, "call_id": "c"})
    )
    assert client.sent_cards == []


@pytest.mark.asyncio
async def test_other_events_ignored():
    sink, client = make_sink()
    from agent.events import RunStarted

    await sink.emit(RunStarted(session_id="s"))
    assert client.sent_texts == []
    assert client.sent_cards == []
