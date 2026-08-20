"""测试 C2：WeChatClient access_token 获取/缓存与消息发送。"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.channels.wechat_client import WeChatClient


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/cgi-bin/gettoken"):
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "access_token": "tok-1"})
    if request.url.path.endswith("/cgi-bin/message/send"):
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
    return httpx.Response(404)


class Recorder:
    """包装 handler 记录每次请求，便于断言请求序列。"""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def make_client(recorder: Recorder) -> WeChatClient:
    return WeChatClient("corp_1", "secret", "1000002", transport=httpx.MockTransport(recorder))


@pytest.mark.asyncio
async def test_send_text_triggers_token_then_message():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    await client.send_text("zhangsan", "你好")

    token_reqs = [r for r in recorder.requests if "/cgi-bin/gettoken" in r.url.path]
    send_reqs = [r for r in recorder.requests if "/cgi-bin/message/send" in r.url.path]
    assert len(token_reqs) == 1
    assert len(send_reqs) == 1
    # 发送请求携带 access_token 参数，消息体为企业微信 text 格式
    assert "access_token=tok-1" in str(send_reqs[0].url)
    body = json.loads(send_reqs[0].read().decode())
    assert body["touser"] == "zhangsan"
    assert body["msgtype"] == "text"
    assert body["text"] == {"content": "你好"}
    assert body["agentid"] == "1000002"


@pytest.mark.asyncio
async def test_token_cached_across_sends():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    await client.send_text("u1", "hi")
    await client.send_text("u2", "hi again")

    token_reqs = [r for r in recorder.requests if "/cgi-bin/gettoken" in r.url.path]
    assert len(token_reqs) == 1  # 第二次发送不再请求 token
    assert len(recorder.requests) == 3


@pytest.mark.asyncio
async def test_send_failure_only_warns():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cgi-bin/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "access_token": "tok-1"})
        return httpx.Response(200, json={"errcode": 93000, "errmsg": "invalid message"})

    client = WeChatClient("corp_1", "secret", "1000002", transport=httpx.MockTransport(handler))
    await client.send_text("u1", "hi")  # 不应抛异常


@pytest.mark.asyncio
async def test_send_card_uses_template_card():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    card = {"card_type": "button_interaction", "main_title": {"title": "工具授权确认"}}
    await client.send_card("zhangsan", card)

    send_reqs = [r for r in recorder.requests if "/cgi-bin/message/send" in r.url.path]
    assert len(send_reqs) == 1
    body = json.loads(send_reqs[0].read().decode())
    assert body["msgtype"] == "template_card"
    assert body["template_card"]["main_title"] == {"title": "工具授权确认"}
