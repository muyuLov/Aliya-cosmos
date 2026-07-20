"""WebSocket 端点处理器"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from core.logger import get_logger

from agent.agent import AliyaAgent, agent_config_from_yaml
from agent.tools.registry import ToolRegistry
from agent.tools.reply import ReplyTool
from agent.tools.memory_query import MemoryQueryTool
from agent.prompts import PromptManager, get_prompt_manager

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
    confirm_callback: Any | None = None,
    prompt_manager: PromptManager | None = None,
) -> AliyaAgent:
    registry = ToolRegistry()
    registry.register(ReplyTool())
    registry.register(MemoryQueryTool())
    # 注：TTS 已由 Agent 在生成最终回复后自动播放，不再作为 LLM 工具

    agent_config = agent_config_from_yaml()

    # 自动初始化分层 Prompt
    if prompt_manager is None:
        prompt_manager = get_prompt_manager()

    return AliyaAgent(
        conversation_service=conversation_service,
        tool_registry=registry,
        memory_manager=memory_manager,
        send_message=send_message,
        tts_service=tts_service,
        audio_player=audio_player,
        audio_relay=audio_relay,
        config=agent_config,
        confirm_callback=confirm_callback,
        prompt_manager=prompt_manager,
    )


def _log_task_error(task: asyncio.Task) -> None:
    """在异步任务因异常结束时记录错误日志，避免异常被静默吞掉。"""
    if not task.cancelled():
        exc = task.exception()
        if exc:
            logger.error("消息处理异常: %s", exc)


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
        # 待处理的确认 Future（WS 模式用）；由 _ws_confirm 创建，_receiver 解析
        pending_confirm: asyncio.Future[bool] | None = None

        async def _send(data: dict) -> None:
            try:
                await websocket.send_json(data)
            except Exception as e:
                logger.warning("WS 发送失败: %s", e)

        async def _ws_confirm(tool_name: str, params: dict) -> bool:
            """WS 模式交互确认：发送 confirm_request 到前端，等待用户的确认响应。"""
            nonlocal pending_confirm
            if pending_confirm is not None:
                # 已有确认流程在进行中，拒绝本次请求
                return False
            pending_confirm = asyncio.get_running_loop().create_future()
            await _send({
                "type": "confirm_request",
                "tool": tool_name,
                "params": params,
            })
            try:
                result = await asyncio.wait_for(pending_confirm, timeout=60.0)
                return bool(result)
            except asyncio.TimeoutError:
                return False
            finally:
                pending_confirm = None

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
                    confirm_callback=_ws_confirm,
                )
            return agent

        async def _cancel_active() -> bool:
            """取消并等待当前消息处理，使 stop 能即时打断进行中的回复。

            返回 True 表示确实取消了活跃任务，False 表示无任务可取消。
            """
            nonlocal active_task
            if active_task is not None and not active_task.done():
                active_task.cancel()
                try:
                    await active_task
                except asyncio.CancelledError:
                    pass
                active_task = None
                return True
            active_task = None
            return False

        async def _keepalive() -> None:
            """仅在空闲（无消息且未在思考）超时后才关闭，释放僵尸连接。"""
            while True:
                elapsed = time.monotonic() - last_activity
                busy = active_task is not None and not active_task.done()

                if not busy and elapsed > _IDLE_TIMEOUT:
                    logger.info("WS 空闲超时，关闭连接")
                    await _send({"type": "notice", "message": "连接空闲超时"})
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                    return

                # 精确睡眠：空闲时按剩余时间，忙碌时保持固定间隔
                if busy:
                    sleep_for = _IDLE_TIMEOUT / 2
                else:
                    sleep_for = max(_IDLE_TIMEOUT - elapsed, 5.0)
                await asyncio.sleep(min(sleep_for, 150.0))

        async def _receiver() -> None:
            # 接收与处理分离：user_message 以后台任务运行（fire-and-forget），
            # 不阻塞 iter_json，使 stop 能在回复进行期间被即时接收并打断。
            nonlocal active_task, last_activity, pending_confirm
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
                        if await _cancel_active():
                            await _send({"type": "notice", "message": "已停止回复"})
                        continue

                    if msg_type == "confirm_response":
                        allowed = data.get("allowed", False)
                        if pending_confirm is not None and not pending_confirm.done():
                            pending_confirm.set_result(allowed)
                        continue

                    if msg_type == "user_message":
                        if active_task is not None and not active_task.done():
                            await _send({"type": "error", "message": "正在处理上一条消息，请稍候或发送 stop"})
                            continue
                        text = (data.get("text") or "").strip()
                        if not text:
                            continue
                        a = _ensure_agent()
                        task = asyncio.create_task(a.handle_user_message(text))
                        task.add_done_callback(_log_task_error)
                        active_task = task
                        continue

                    if msg_type == "set_style":
                        style = str(data.get("style", "default"))
                        _ensure_agent().set_style(style)
                        await _send({"type": "style_changed", "style": style})
                        continue

                    if msg_type == "set_emotion":
                        feeling = str(data.get("feeling", ""))
                        _ensure_agent().set_emotion(feeling)
                        await _send({"type": "emotion_changed", "feeling": feeling})
                        continue

                    if msg_type == "get_prompt_config":
                        config = _ensure_agent().get_prompt_config()
                        await _send({"type": "prompt_config", **config})
                        continue

                    if msg_type == "get_feeling_scores":
                        scores = _ensure_agent().get_feeling_scores()
                        await _send({"type": "feeling_scores", "scores": scores, "dominant": _ensure_agent().get_emotion()})
                        continue

                    logger.debug("忽略未知 WS 消息类型: %s", msg_type)

            except WebSocketDisconnect:
                logger.info("WS 客户端已断开")
            except json.JSONDecodeError:
                logger.warning("WS 收到非法 JSON 数据，关闭连接")
            except Exception as e:
                logger.error("WS 接收异常: %s", e, exc_info=True)

        recv_task = asyncio.create_task(_receiver())
        keepalive_task = asyncio.create_task(_keepalive())

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
