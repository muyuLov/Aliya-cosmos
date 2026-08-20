"""测试 B2：FeishuClient token 获取/缓存与消息发送。"""

from __future__ import annotations

import json

import httpx
import pytest

from agent.channels.feishu_client import FeishuClient


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
        return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-123"})
    if request.url.path.endswith("/im/v1/messages"):
        return httpx.Response(200, json={"code": 0, "msg": "ok"})
    return httpx.Response(404)


class Recorder:
    """包装 handler 记录每次请求，便于断言请求序列。"""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def make_client(recorder: Recorder) -> FeishuClient:
    return FeishuClient("app_id", "app_secret", transport=httpx.MockTransport(recorder))


@pytest.mark.asyncio
async def test_send_text_triggers_token_then_message():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    await client.send_text("oc_1", "你好")

    token_reqs = [r for r in recorder.requests if "tenant_access_token" in r.url.path]
    send_reqs = [r for r in recorder.requests if "/im/v1/messages" in r.url.path]
    assert len(token_reqs) == 1
    assert len(send_reqs) == 1
    # 发送请求携带 Bearer token 且 content 为 JSON 字符串（httpx 序列化为紧凑格式）
    assert send_reqs[0].headers["Authorization"] == "Bearer t-123"
    body = send_reqs[0].read().decode()
    assert '"receive_id":"oc_1"' in body
    assert '"msg_type":"text"' in body


@pytest.mark.asyncio
async def test_token_cached_across_sends():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    await client.send_text("oc_1", "hi")
    await client.send_text("oc_2", "hi again")

    token_reqs = [r for r in recorder.requests if "tenant_access_token" in r.url.path]
    assert len(token_reqs) == 1  # 第二次发送不再请求 token
    assert len(recorder.requests) == 3


@pytest.mark.asyncio
async def test_send_failure_only_warns():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-123"})
        return httpx.Response(200, json={"code": 190001, "msg": "bad request"})

    client = FeishuClient("app_id", "app_secret", transport=httpx.MockTransport(handler))
    await client.send_text("oc_1", "hi")  # 不应抛异常


@pytest.mark.asyncio
async def test_send_card_uses_interactive_type():
    recorder = Recorder(_ok_handler)
    client = make_client(recorder)
    card = {"type": "template_card", "data": {"template_card": {"card_type": "button_interaction"}}}
    await client.send_card("oc_1", card)

    send_reqs = [r for r in recorder.requests if "/im/v1/messages" in r.url.path]
    assert len(send_reqs) == 1
    body = json.loads(send_reqs[0].read().decode())
    assert body["msg_type"] == "interactive"
    # content 字段为 JSON 字符串（飞书 API 约束），内层为卡片结构
    inner = json.loads(body["content"])
    assert inner["data"]["template_card"]["card_type"] == "button_interaction"
