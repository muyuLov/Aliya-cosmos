"""WebSocket 端点处理器"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from core.logger import get_logger

from agent.agent import AliyaAgent
from agent.tools.registry import ToolRegistry
from agent.tools.reply import ReplyTool
from agent.tools.memory_query import MemoryQueryTool

logger = get_logger(__name__)

# 空闲超时（秒）：仅在处理空闲时触发，避免长思考期间被误杀
_IDLE_TIMEOUT = 300.0


def build_agent(
    conversation_service: Any,
    send_message: Any,
    tts_service: Any | None = None,
    memory_manager: Any | None = None,
    audio_player: Any | None = None,
    audio_relay: Any | None = None,
) -> AliyaAgent:
    registry = ToolRegistry()
    registry.register(ReplyTool())
    registry.register(MemoryQueryTool())
    # 注：TTS 已由 Agent 在生成最终回复后自动播放，不再作为 LLM 工具

    return AliyaAgent(
        conversation_service=conversation_service,
        tool_registry=registry,
        memory_manager=memory_manager,
        send_message=send_message,
        tts_service=tts_service,
        audio_player=audio_player,
        audio_relay=audio_relay,
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
        active_task: asyncio.Task | None = None
        last_activity = time.monotonic()

        async def _send(data: dict) -> None:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.warning("WS 发送失败: %s", e)

        def _ensure_agent() -> AliyaAgent:
            nonlocal agent
            if agent is None:
                conv = conversation_service_factory()
                agent = build_agent(
                    conversation_service=conv,
                    send_message=_send,
                    audio_relay=_send,
                    tts_service=tts_service,
                    memory_manager=memory_manager,
                    audio_player=audio_player,
                )
            return agent

        async def _cancel_active() -> None:
            """取消并等待当前消息处理，使 stop 能即时打断进行中的回复。"""
            nonlocal active_task
            if active_task is not None and not active_task.done():
                active_task.cancel()
                try:
                    await active_task
                except asyncio.CancelledError:
                    pass
                await _send({"type": "notice", "message": "已停止回复"})
            active_task = None

        async def _keepalive() -> None:
            """仅在空闲（无消息且未在思考）超时后才关闭，释放僵尸连接。"""
            while True:
                await asyncio.sleep(_IDLE_TIMEOUT / 2)
                busy = active_task is not None and not active_task.done()
                if not busy and time.monotonic() - last_activity > _IDLE_TIMEOUT:
                    logger.info("WS 空闲超时，关闭连接")
                    await _send({"type": "notice", "message": "连接空闲超时"})
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    return

        async def _receiver() -> None:
            # 接收与处理分离：user_message 以后台任务运行（fire-and-forget），
            # 不阻塞 iter_json，使 stop 能在回复进行期间被即时接收并打断。
            nonlocal active_task, last_activity
            try:
                async for data in websocket.iter_json():
                    last_activity = time.monotonic()

                    if not isinstance(data, dict):
                        await _send({"type": "error", "message": "无效消息格式（应为 JSON 对象）"})
                        continue

                    msg_type = data.get("type", "")

                    if msg_type == "ping":
                        await _send({"type": "pong"})
                        continue

                    if msg_type == "stop":
                        await _cancel_active()
                        continue

                    if msg_type == "user_message":
                        if active_task is not None and not active_task.done():
                            await _send({"type": "error", "message": "正在处理上一条消息，请稍候或发送 stop"})
                            continue
                        text = (data.get("text") or "").strip()
                        if not text:
                            continue
                        a = _ensure_agent()
                        active_task = asyncio.ensure_future(a.handle_user_message(text))
                        continue

                    logger.debug("忽略未知 WS 消息类型: %s", msg_type)

            except WebSocketDisconnect:
                logger.info("WS 客户端已断开")
            except json.JSONDecodeError:
                logger.warning("WS 收到非法 JSON 数据，关闭连接")
            except Exception as e:
                logger.error("WS 接收异常: %s", e, exc_info=True)

        recv_task = asyncio.ensure_future(_receiver())
        keepalive_task = asyncio.ensure_future(_keepalive())

        try:
            await recv_task
        finally:
            if not recv_task.done():
                recv_task.cancel()
            await _cancel_active()
            if not keepalive_task.done():
                keepalive_task.cancel()
            try:
                await websocket.close()
            except Exception:
                pass

    return handle_connection
