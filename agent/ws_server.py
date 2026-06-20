"""WebSocket 服务器：管理客户端连接与会话生命周期。"""

from __future__ import annotations

import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from agent.aliya_agent_thread import AliyaAgentThread
from core.config import get_config_instance
from core.logger import get_logger

logger = get_logger(__name__)


# ── 会话管理（支持断连重连）─────────────────────────────────────────────────

class _ActiveSession:
    def __init__(self, session: AliyaAgentThread) -> None:
        self.session = session
        self.last_active: float = 0.0


_sessions: dict[str, _ActiveSession] = {}
_sessions_lock = asyncio.Lock()
_SESSION_TTL = 300


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

    async with _sessions_lock:
        active = _sessions.get(session_id)
        if active is not None and active.session.is_alive:
            session = active.session
            session.reset_output()
            logger.info("WS重连 | session_id=%s | client=%s", session_id, websocket.client)
        else:
            session = AliyaAgentThread(config_path, asyncio.get_running_loop())
            await asyncio.to_thread(session.start)
            active = _ActiveSession(session)
            _sessions[session_id] = active
            logger.info("WS新建会话 | session_id=%s | client=%s", session_id, websocket.client)

    active.last_active = asyncio.get_running_loop().time()

    async def receive_loop() -> None:
        while True:
            try:
                payload = await asyncio.wait_for(
                    websocket.receive_json(), timeout=ws_timeout,
                )
            except asyncio.TimeoutError:
                raise WebSocketDisconnect(1000, f"接收超时 ({ws_timeout}s)")
            await session.dispatch(payload)

    async def send_loop() -> None:
        while True:
            await websocket.send_json(await session.output.receive())

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
        logger.info("WS断开 | session_id=%s | 保留会话 %ds 等待重连",
                     session_id, int(reconnect_timeout))
        active.last_active = asyncio.get_running_loop().time()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Agent连接处理器崩溃 | session_id=%s", session_id)
    finally:
        receive_task.cancel()
        send_task.cancel()
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

    async def on_startup() -> None:
        asyncio.create_task(_cleanup_stale_sessions())

    app.add_event_handler("startup", on_startup)
    uvicorn.run(app, host=ws_host, port=ws_port, log_level=log_level)


if __name__ == "__main__":
    main()
