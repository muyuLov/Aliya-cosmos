"""测试钩子注册表：注册 / 触发 / 异常隔离 / 异步可丢调度"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.hooks import HookPoint, HookRegistry


@pytest.mark.asyncio
async def test_register_and_run_in_order():
    registry = HookRegistry()
    calls: list[str] = []

    async def h1(_text: str) -> None:
        calls.append("h1")

    async def h2(_text: str) -> None:
        calls.append("h2")

    registry.register(HookPoint.BEFORE_TURN, h1)
    registry.register(HookPoint.BEFORE_TURN, h2)
    await registry.run(HookPoint.BEFORE_TURN, "hi")
    assert calls == ["h1", "h2"]


@pytest.mark.asyncio
async def test_run_isolates_handler_exception():
    registry = HookRegistry()

    async def boom(*_args: Any) -> None:
        raise RuntimeError("boom")

    async def ok(*_args: Any) -> None:
        pass

    registry.register(HookPoint.AFTER_TOOL, boom)
    registry.register(HookPoint.AFTER_TOOL, ok)
    # 异常被隔离，后续钩子仍执行，run 不抛出
    await registry.run(HookPoint.AFTER_TOOL, "t", None)


@pytest.mark.asyncio
async def test_run_later_fire_and_forget():
    registry = HookRegistry()
    done = asyncio.Event()

    async def slow(*_args: Any) -> None:
        await asyncio.sleep(0.01)
        done.set()

    registry.register(HookPoint.AFTER_REPLY, slow)
    registry.run_later(HookPoint.AFTER_REPLY, "reply")
    await asyncio.wait_for(done.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_unregister():
    registry = HookRegistry()

    async def h(*_args: Any) -> None:
        raise AssertionError("不应被调用")

    registry.register(HookPoint.AFTER_TURN, h)
    registry.unregister(HookPoint.AFTER_TURN, h)
    await registry.run(HookPoint.AFTER_TURN, "reply")


@pytest.mark.asyncio
async def test_run_supports_sync_handler():
    """同步处理器（如 cognition 钩子）可直接执行且被异常隔离。"""
    registry = HookRegistry()
    calls: list[str] = []

    def sync_h(text: str) -> None:
        calls.append(text)

    registry.register(HookPoint.BEFORE_TURN, sync_h)
    await registry.run(HookPoint.BEFORE_TURN, "hi")
    assert calls == ["hi"]
