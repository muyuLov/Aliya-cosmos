"""WebSocket 端点处理器"""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from core.logger import get_logger

from agent.agent import AliyaAgent
from agent.tools.registry import ToolRegistry
from agent.tools.reply import ReplyTool
from agent.tools.tts_speak import TTSTool
from agent.tools.memory_query import MemoryQueryTool

logger = get_logger(__name__)


def build_agent(
    conversation_service: Any,
    send_message: Any,
    tts_service: Any | None = None,
    memory_manager: Any | None = None,
    audio_player: Any | None = None,
) -> AliyaAgent:
    registry = ToolRegistry()
    registry.register(ReplyTool())
    registry.register(MemoryQueryTool())
    if tts_service is not None:
        registry.register(TTSTool())

    return AliyaAgent(
        conversation_service=conversation_service,
        tool_registry=registry,
        memory_manager=memory_manager,
        send_message=send_message,
        tts_service=tts_service,
        audio_player=audio_player,
    )


def create_handler(
    conversation_service_factory: Any,
    tts_service: Any | None = None,
    memory_manager: Any | None = None,
    audio_player: Any | None = None,
) -> Any:
    async def handle_connection(websocket: WebSocket) -> None:
        await websocket.accept()
        logger.info("WS 客户端已连接")

        agent: AliyaAgent | None = None

        async def _send(data: dict) -> None:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.warning("WS 发送失败: %s", e)

        try:
            async for data in websocket.iter_json():
                msg_type = data.get("type", "")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if msg_type == "user_message":
                    text = data.get("text", "").strip()
                    if not text:
                        continue

                    if agent is None:
                        conv = conversation_service_factory()
                        agent = build_agent(
                            conversation_service=conv,
                            send_message=_send,
                            tts_service=tts_service,
                            memory_manager=memory_manager,
                            audio_player=audio_player,
                        )

                    await agent.handle_user_message(text)
                    continue

                if msg_type == "stop":
                    if agent is not None:
                        agent.cancel_background_tasks()
                    continue

        except WebSocketDisconnect:
            logger.info("WS 客户端已断开")
        except Exception as e:
            logger.error("WS 处理器异常: %s", e, exc_info=True)
        finally:
            if agent is not None:
                agent.cancel_background_tasks()

    return handle_connection
