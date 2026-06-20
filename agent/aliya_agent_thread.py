"""AliyaAgent 线程管理：独立线程中运行 AliyaAgent，通过 OutputChannel 输出事件。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

from agent.agent import AliyaAgent
from agent.brain import BrainEngine
from agent.tools.base import ToolCategory
from agent.tools import ToolLoader
from core.config import get_config_instance
from core.logger import get_logger
from core.tts import create_from_config
from memory import get_memory_manager

logger = get_logger(__name__)

# 消息类型 → (处理方法名, 参数字段, 类型转换)
_MESSAGE_HANDLERS: dict[str, tuple[str, str | None, type | None]] = {
    "user_message": ("handle_user_message", "text", str),
    "stop": ("handle_stop", None, None),
    "clear_history": ("handle_clear_history", "confirm", bool),
    "ping": ("handle_ping", None, None),
    "get_stats": ("handle_get_stats", None, None),
}


class OutputChannel:
    """线程安全的输出通道。

    Agent 通过 send() 推送事件，send_loop 通过 receive() 消费。
    send() 可从任意线程调用，receive() 须在事件循环中调用。

    使用 generation 计数器确保 reset() 后已调度的回调不会写入旧队列。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._gen = 0

    def send(self, data: dict[str, Any]) -> None:
        gen = self._gen
        self._loop.call_soon_threadsafe(self._put, data, gen)

    def _put(self, data: dict[str, Any], gen: int) -> None:
        if gen != self._gen:
            return
        self._queue.put_nowait(data)

    async def receive(self) -> dict[str, Any]:
        return await self._queue.get()

    def reset(self) -> None:
        """丢弃旧队列，断连重连时使用。"""
        self._queue = asyncio.Queue()
        self._gen += 1


class AliyaAgentThread:
    """在独立线程中运行 AliyaAgent。"""

    def __init__(self, config_path: str, output_loop: asyncio.AbstractEventLoop) -> None:
        self._config_path = config_path
        self._output = OutputChannel(output_loop)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready: Future[None] = Future()
        self._agent: AliyaAgent | None = None
        self._brain: BrainEngine | None = None
        self._tts_service: Any = None
        self._audio_player: Any = None

    @property
    def output(self) -> OutputChannel:
        return self._output

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("Agent thread is not running")
        return self._loop

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="Aliya", daemon=True)
        self._thread.start()
        self._ready.result(timeout=30.0)

    def reset_output(self) -> None:
        self._output.reset()

    async def dispatch(self, payload: dict[str, Any]) -> None:
        loop = self._require_loop()
        future = asyncio.run_coroutine_threadsafe(self._handle_payload(payload), loop)
        await asyncio.wrap_future(future)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def stop(self) -> None:
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        await asyncio.wrap_future(future)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5.0)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._setup())
            self._ready.set_result(None)
            loop.run_forever()
        except Exception as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
            logger.exception("Agent 线程启动失败")
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    async def _setup(self) -> None:
        config = get_config_instance(self._config_path)
        tool_timeout = float(config.get("cosmos.service.agent.tools.timeout_seconds", 120.0))
        top_k = int(config.get("cosmos.service.agent.grag.top_k", 5))
        self._tts_service, self._audio_player = create_from_config(self._config_path)
        memory_manager = get_memory_manager()

        registry = ToolLoader.build_default_registry(
            timeout_seconds=tool_timeout,
            injections={
                "tts_service": self._tts_service,
                "audio_player": self._audio_player,
                "memory_manager": memory_manager,
                "output_channel": self._output,
            },
        )
        visible_categories = {ToolCategory.CORE}
        internal_tools: dict[str, Any] = {}
        if memory_manager is not None:
            from agent.tools.advanced import MemoryQueryTool
            internal_tools["memory_query"] = MemoryQueryTool(memory_manager)

        self._brain = BrainEngine.from_config(
            config_path=self._config_path,
            tool_descriptions=registry.format_descriptions(category_filter=visible_categories),
            memory_manager=memory_manager,
            max_iterations=int(config.get("cosmos.service.agent.brain.max_iterations", 5)),
            internal_tools=internal_tools,
            visible_categories=visible_categories,
        )

        await self._tts_service.__aenter__()
        await self._audio_player.__aenter__()
        await self._brain.__aenter__()

        self._agent = AliyaAgent(
            brain=self._brain,
            tool_registry=registry,
            memory_manager=memory_manager,
            output=self._output.send,
            top_k=top_k,
        )
        logger.info("Agent 线程已启动 | thread=%s", threading.current_thread().name)

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if self._agent is None:
            raise RuntimeError("Agent is not initialized")

        info = _MESSAGE_HANDLERS.get(payload.get("type"))
        if info is not None:
            method_name, arg_key, cast = info
            if arg_key and cast:
                await getattr(self._agent, method_name)(cast(payload.get(arg_key, cast())))
            else:
                await getattr(self._agent, method_name)()
        else:
            self._output.send({
                "type": "brain_error",
                "code": "UNSUPPORTED_MESSAGE_TYPE",
                "step": "message_routing",
                "message": f"unsupported message type: {payload.get('type')}",
            })

    async def _shutdown(self) -> None:
        if self._agent is not None:
            await self._agent.aclose()
        if self._brain is not None:
            await self._brain.__aexit__(None, None, None)
        if self._audio_player is not None:
            await self._audio_player.__aexit__(None, None, None)
        if self._tts_service is not None:
            await self._tts_service.__aexit__(None, None, None)
        logger.info("Agent 线程资源已释放")
