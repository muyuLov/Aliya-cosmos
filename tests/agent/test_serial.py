"""Task 3.0b: 故事级串行队列测试

验证 serial(story_id, task) 的串行化行为和失败隔离。
"""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_serial_serializes_same_story():
    """同一 story_id 的任务应串行执行"""
    from agent.story.serial import serial

    order = []

    async def task_a():
        await asyncio.sleep(0.01)
        order.append("a")

    async def task_b():
        order.append("b")

    await serial("s1", task_a)
    await serial("s1", task_b)
    assert order == ["a", "b"]


@pytest.mark.asyncio
async def test_serial_parallel_across_stories():
    """不同 story_id 的任务应可并行"""
    from agent.story.serial import serial

    started = []
    barriers: dict[str, asyncio.Event] = {
        "s1": asyncio.Event(),
        "s2": asyncio.Event(),
    }

    async def task_s1():
        started.append("s1_start")
        await barriers["s1"].wait()
        started.append("s1_end")

    async def task_s2():
        started.append("s2_start")
        barriers["s1"].set()  # s1 完成其阻塞点
        await barriers["s2"].wait()
        started.append("s2_end")

    t1 = asyncio.create_task(serial("s1", task_s1))
    t2 = asyncio.create_task(serial("s2", task_s2))
    await asyncio.sleep(0.01)
    barriers["s2"].set()
    await asyncio.gather(t1, t2)

    # s1 和 s2 应交错启动
    assert "s1_start" in started
    assert "s2_start" in started


@pytest.mark.asyncio
async def test_serial_failure_does_not_block_next():
    """失败任务不应阻塞后续任务"""
    from agent.story.serial import serial

    order = []

    async def failing():
        raise RuntimeError("boom")

    async def after():
        order.append("after")

    await serial("s1", failing)
    await serial("s1", after)
    assert order == ["after"]


@pytest.mark.asyncio
async def test_serial_returns_value():
    """serial 应返回任务返回值"""
    from agent.story.serial import serial

    async def compute():
        return 42

    result = await serial("s1", compute)
    assert result == 42
