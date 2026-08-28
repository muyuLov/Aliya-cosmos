"""Task 3.0: 写队列测试

验证 WriteQueue 串行化写入 + 退避重试。
"""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_write_queue_serializes():
    """WriteQueue 应串行执行写入任务"""
    from agent.story.write_queue import WriteQueue

    queue = WriteQueue()
    results = []

    async def job_a():
        await asyncio.sleep(0.01)
        results.append("a")

    async def job_b():
        results.append("b")

    await queue.submit(job_a)
    await queue.submit(job_b)
    await queue.drain()

    assert results == ["a", "b"]


@pytest.mark.asyncio
async def test_write_queue_retries_on_transient_error():
    """瞬态异常应触发退避重试，最多 7 次"""
    from agent.story.write_queue import WriteQueue

    queue = WriteQueue()
    attempt = 0

    async def flaky_job():
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise ConnectionError("transient")
        return "ok"

    result = await queue.submit(flaky_job)
    await queue.drain()

    assert result == "ok"
    assert attempt == 3


@pytest.mark.asyncio
async def test_write_queue_gives_up_after_max_retries():
    """持续失败应放弃并记录错误"""
    from agent.story.write_queue import WriteQueue

    queue = WriteQueue()

    async def always_fail():
        raise ConnectionError("permanent")

    result = await queue.submit(always_fail)
    await queue.drain()

    # 返回 None 表示最终失败
    assert result is None
