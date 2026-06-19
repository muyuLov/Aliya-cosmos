from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent.agent import AliyaAgent
from agent.brain import BrainEngine
from agent.tools import ToolLoader
from core.config import get_config_instance
from core.logger import get_logger
from core.tts import create_from_config
from memory import get_memory_manager

logger = get_logger(__name__)


class WebSocketOutputProxy:
    """线程安全输出代理：Agent 线程通过它把消息投递到 WebSocket 线程。"""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        self._loop = loop
        self._queue = queue

    async def send_json(self, data: dict[str, Any]) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, data)

    def replace_queue(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """替换输出队列（重连时使用，旧队列中的消息将被丢弃）。"""
        self._queue = queue


class AgentThreadSession:
    """在独立线程中运行 AliyaAgent，并通过线程安全调用接收 WebSocket 消息。"""

    def __init__(
        self,
        config_path: str,
        output_loop: asyncio.AbstractEventLoop,
        output_queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        self._config_path = config_path
        self._output_loop = output_loop
        self._output_queue = output_queue
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready: Future[None] = Future()
        self._agent: AliyaAgent | None = None
        self._brain: BrainEngine | None = None
        self._tts_service: Any = None
        self._audio_player: Any = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._thread_main,
            name="Aliya",
            daemon=True,
        )
        self._thread.start()
        self._ready.result(timeout=30.0)

    async def dispatch(self, payload: dict[str, Any]) -> None:
        if self._loop is None:
            raise RuntimeError("Agent thread is not running")
        future = asyncio.run_coroutine_threadsafe(self._handle_payload(payload), self._loop)
        await asyncio.wrap_future(future)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def replace_output_queue(self, new_queue: asyncio.Queue[dict[str, Any]]) -> None:
        """在 Agent 线程中将 websocket 代理的输出队列替换为新队列（重连时调用）。"""
        if self._loop is None:
            raise RuntimeError("Agent thread is not running")
        future = asyncio.run_coroutine_threadsafe(self._replace_queue(new_queue), self._loop)
        await asyncio.wrap_future(future)

    async def _replace_queue(self, new_queue: asyncio.Queue[dict[str, Any]]) -> None:
        if self._agent is not None:
            self._agent._websocket.replace_queue(new_queue)

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
            },
        )
        self._brain = BrainEngine.from_config(
            config_path=self._config_path,
            tool_descriptions=registry.format_descriptions(),
            memory_manager=memory_manager,
            max_iterations=int(config.get("cosmos.service.agent.brain.max_iterations", 5)),
        )

        await self._tts_service.__aenter__()
        await self._audio_player.__aenter__()
        await self._brain.__aenter__()

        self._agent = AliyaAgent(
            brain=self._brain,
            tool_registry=registry,
            memory_manager=memory_manager,
            websocket=WebSocketOutputProxy(self._output_loop, self._output_queue),
            top_k=top_k,
        )
        logger.info("Agent 线程已启动 | thread=%s", threading.current_thread().name)

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if self._agent is None:
            raise RuntimeError("Agent is not initialized")

        message_type = payload.get("type")
        if message_type == "user_message":
            await self._agent.handle_user_message(str(payload.get("text", "")))
        elif message_type == "stop":
            await self._agent.handle_stop()
        elif message_type == "clear_history":
            await self._agent.handle_clear_history(bool(payload.get("confirm", False)))
        elif message_type == "ping":
            await self._agent.handle_ping()
        elif message_type == "get_stats":
            await self._agent.handle_get_stats()
        else:
            await self._agent._websocket.send_json(
                {
                    "type": "brain_error",
                    "code": "UNSUPPORTED_MESSAGE_TYPE",
                    "step": "message_routing",
                    "message": f"unsupported message type: {message_type}",
                }
            )

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


class WebSocketInputHandler:
    def __init__(
        self,
        websocket: WebSocket,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._websocket = websocket
        self._timeout_seconds = timeout_seconds

    async def receive_json(self) -> dict:
        try:
            return await asyncio.wait_for(
                self._websocket.receive_json(),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            raise WebSocketDisconnect(1000, f"接收超时 ({self._timeout_seconds}s)") from e


# ── 会话管理（支持断连重连）─────────────────────────────────────────────────

class _ActiveSession:
    def __init__(self, session: AgentThreadSession) -> None:
        self.session = session
        self.last_active: float = 0.0

_sessions: dict[str, _ActiveSession] = {}
_sessions_lock = asyncio.Lock()
_SESSION_TTL = 300  # 5 分钟无活动后清理


async def _cleanup_stale_sessions() -> None:
    """后台任务：定期清理过期的空闲会话。"""
    while True:
        await asyncio.sleep(60)
        now = asyncio.get_running_loop().time()
        async with _sessions_lock:
            stale = [
                sid for sid, s in _sessions.items()
                if now - s.last_active > _SESSION_TTL
            ]
            for sid in stale:
                try:
                    await _sessions[sid].session.stop()
                except Exception:
                    logger.exception("清理过期会话失败 | session_id=%s", sid)
                del _sessions[sid]
            if stale:
                logger.info("清理 %d 个过期会话", len(stale))


async def handle_connection(
    websocket: WebSocket, config_path: str = "data/config/main.yml"
) -> None:
    await websocket.accept()
    session_id = str(websocket.query_params.get("session_id", "default"))
    config = get_config_instance(config_path)
    ws_timeout = float(config.get("cosmos.service.agent.ws_server.timeout", 60.0))
    reconnect_timeout = float(config.get("cosmos.service.agent.ws_server.reconnect_timeout", 120.0))

    output_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async with _sessions_lock:
        active = _sessions.get(session_id)
        if active is not None and active.session.is_alive:
            session = active.session
            await session.replace_output_queue(output_queue)
            logger.info("WS重连 | session_id=%s | client=%s", session_id, websocket.client)
        else:
            session = AgentThreadSession(config_path, asyncio.get_running_loop(), output_queue)
            await asyncio.to_thread(session.start)
            active = _ActiveSession(session)
            _sessions[session_id] = active
            logger.info("WS新建会话 | session_id=%s | client=%s", session_id, websocket.client)

    active.last_active = asyncio.get_running_loop().time()

    handler = WebSocketInputHandler(websocket, timeout_seconds=ws_timeout)

    async def receive_loop() -> None:
        while True:
            payload = await handler.receive_json()
            await session.dispatch(payload)

    async def send_loop() -> None:
        while True:
            await websocket.send_json(await output_queue.get())

    receive_task = asyncio.create_task(receive_loop())
    send_task = asyncio.create_task(send_loop())

    try:
        done, pending = await asyncio.wait(
            {receive_task, send_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        logger.info(
            "WS断开 | session_id=%s | 保留会话 %ds 等待重连",
            session_id, int(reconnect_timeout),
        )
        active.last_active = asyncio.get_running_loop().time()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Agent连接处理器崩溃 | session_id=%s", session_id)
    finally:
        receive_task.cancel()
        send_task.cancel()
        # 不主动 stop 会话 — 等待重连或超时清理
        active.last_active = asyncio.get_running_loop().time()


def main() -> None:
    """直接运行本文件时启动 WebSocket 服务。"""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.routing import APIWebSocketRoute

    config = get_config_instance("data/config/main.yml")
    log_level = config.get("cosmos.logger.level", "info")
    ws_host = config.get("cosmos.service.agent.ws_server.host", "127.0.0.1")
    ws_port = int(config.get("cosmos.service.agent.ws_server.port", 8765))

    app = FastAPI()
    app.router.routes.append(APIWebSocketRoute("/agent/ws", handle_connection))
    logger.info("Agent WebSocket 服务启动 | ws=%s:%d | log=%s", ws_host, ws_port, log_level)

    # 启动过期会话清理后台任务
    async def on_startup() -> None:
        asyncio.create_task(_cleanup_stale_sessions())

    app.add_event_handler("startup", on_startup)

    uvicorn.run(app, host=ws_host, port=ws_port, log_level=log_level)


if __name__ == "__main__":
    main()
