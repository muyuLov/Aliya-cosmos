import asyncio

from agent.queue import SessionQueue


async def test_enqueue_serializes_messages():
    queue = SessionQueue()
    order = []

    async def loop_fn(text, images=None):  # pyright: ignore[reportUnusedParameter]
        order.append(text)
        await asyncio.sleep(0.01)
        return text

    await queue.start(loop_fn)
    await queue.enqueue("first")
    await queue.enqueue("second")
    await asyncio.sleep(0.1)
    await queue.stop()
    assert order == ["first", "second"]
