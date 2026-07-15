"""针对 agent/ws.py create_handler 的协议层单测

通过 mock 底层 WebSocket（iter_json / send_json）与 AliyaAgent.handle_user_message，
验证 WS 协议的正确性。为避免依赖 pytest-asyncio 插件，测试以同步函数包装 asyncio.run 执行。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import agent.agent as _agent_mod
from agent.ws import create_handler


class _FakeIter:
    """模拟 websocket.iter_json 的异步生成器：顺序产出消息，或抛指定异常"""

    def __init__(self, items, raise_exc=None):
        self._items = list(items)
        self._idx = 0
        self._raise_exc = raise_exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        # 模拟真实 iter_json 每次接收都会 await 网络：强制让出事件循环，
        # 使已 ensure_future 的后台任务有机会运行，stop 才能打断进行中的回复。
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


def _patch_agent(monkeypatch, fn):
    # fn 签名 (text)；经 AsyncMock 包裹以屏蔽 self 绑定，使 ensure_future 可调度
    monkeypatch.setattr(_agent_mod.AliyaAgent, "handle_user_message", AsyncMock(side_effect=fn))


@pytest.fixture
def factory():
    return lambda: MagicMock()


def test_ping_returns_pong(factory):
    ws = _make_ws([{"type": "ping"}])
    asyncio.run(create_handler(factory)(ws))
    assert {"type": "pong"} in _sent(ws)


def test_unknown_type_ignored(factory):
    ws = _make_ws([{"type": "mystery"}])
    asyncio.run(create_handler(factory)(ws))
    assert not any(m.get("type") == "error" for m in _sent(ws))


def test_non_dict_message_returns_error(factory):
    ws = _make_ws([[1, 2, 3]])
    asyncio.run(create_handler(factory)(ws))
    assert any(m.get("type") == "error" for m in _sent(ws))


def test_invalid_json_closes_gracefully(factory):
    ws = _make_ws([{"type": "ping"}], raise_exc=json.JSONDecodeError("bad", "", 0))
    asyncio.run(create_handler(factory)(ws))  # 非法 JSON 应被捕获，连接优雅关闭而非崩溃


def test_user_message_invokes_agent(factory, monkeypatch):
    ws = _make_ws([{"type": "user_message", "text": "你好"}])
    called = {}

    async def fake(text):
        called["text"] = text

    _patch_agent(monkeypatch, fake)
    asyncio.run(create_handler(factory)(ws))
    assert called.get("text") == "你好"


def test_stop_cancels_active_task(factory, monkeypatch):
    ws = _make_ws([{"type": "user_message", "text": "hi"}, {"type": "stop"}])
    state = {"cancelled": False}

    async def slow(text):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    _patch_agent(monkeypatch, slow)

    async def _run():
        await asyncio.wait_for(create_handler(factory)(ws), timeout=5)

    asyncio.run(_run())
    assert state["cancelled"] is True
    assert any(
        m.get("type") == "notice" and "停止" in m.get("message", "")
        for m in _sent(ws)
    )


def test_concurrent_user_message_rejected(factory, monkeypatch):
    ws = _make_ws([
        {"type": "user_message", "text": "a"},
        {"type": "user_message", "text": "b"},
    ])
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(text):
        started.set()
        await release.wait()

    _patch_agent(monkeypatch, slow)

    async def _run():
        task = asyncio.ensure_future(create_handler(factory)(ws))
        await started.wait()
        await asyncio.sleep(0.05)  # 让第二条消息在 active_task 未完成时被处理
        release.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(_run())
    assert any(m.get("type") == "error" for m in _sent(ws))
