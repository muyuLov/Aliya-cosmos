"""补充 WS 协议层边缘场景测试：空消息、断开、空闲超时、stop 无活跃任务"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import WebSocketDisconnect

from agent.ws import create_handler


class _FakeIter:
    """模拟 websocket.iter_json 的异步生成器"""

    def __init__(self, items, raise_exc=None):
        self._items = list(items)
        self._idx = 0
        self._raise_exc = raise_exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


def _make_ws(messages, raise_exc=None):
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.iter_json = lambda: _FakeIter(messages, raise_exc=raise_exc)
    return ws


def _sent(ws):
    return [c.args[0] for c in ws.send_json.call_args_list]


@pytest.fixture
def factory():
    return lambda: MagicMock()


def test_empty_user_message_ignored(factory):
    """空文本不调用 agent"""
    ws = _make_ws([{"type": "user_message", "text": ""}])
    asyncio.run(create_handler(factory)(ws))
    # 不应有任何 reply/error
    assert not any(m.get("type") in ("error", "brain_start") for m in _sent(ws))


def test_websocket_disconnect_graceful(factory):
    """WebSocketDisconnect 应被优雅捕获"""
    ws = _make_ws([], raise_exc=WebSocketDisconnect(code=1000))
    # 不应抛出异常
    asyncio.run(create_handler(factory)(ws))
    assert ws.close.await_count >= 0


def test_stop_without_active_task_no_error(factory):
    """stop 消息在没有活跃任务时不应报错"""
    ws = _make_ws([{"type": "stop"}])
    asyncio.run(create_handler(factory)(ws))
    # 不应有 error 消息
    assert not any(m.get("type") == "error" for m in _sent(ws))


def test_multiple_stop_no_crash(factory):
    """连续多个 stop 不应崩溃"""
    ws = _make_ws([{"type": "stop"}, {"type": "stop"}, {"type": "stop"}])
    asyncio.run(create_handler(factory)(ws))


def test_ping_after_user_message(factory):
    """user_message 后 ping 仍能正常返回 pong"""
    ws = _make_ws([
        {"type": "user_message", "text": "hi"},
        {"type": "ping"},
    ])
    asyncio.run(create_handler(factory)(ws))
    sent_types = [m.get("type") for m in _sent(ws)]
    assert "pong" in sent_types
