"""WS 网关：AG-UI 风格事件流收发，/agent/ws 端点

职责：
- 接收循环：客户端消息分发（user_message / stop / confirm_response / ping）
- 发送循环：队列事件序列化为线上协议 JSON 推送给客户端
- 连接级会话：经 build_session_factory 获取/创建 AgentSession
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.events import (
    ERROR,
    NOTICE,
    TOKEN_USAGE,
    AgentEvent,
    ProtocolEvent,
    to_protocol,
)
from agent.session import (
    AgentSession,
    SessionFactory,
    _get_or_create_session_store,
    build_session_factory,
)


def create_ws_router(session_factory: SessionFactory | None = None) -> APIRouter:
    """创建 WS 路由；session_factory 可注入（测试用 mock，生产用默认装配）。"""
    router = APIRouter()

    if session_factory is None:
        session_factory = build_session_factory()

    @router.websocket("/agent/ws")
    async def agent_ws(ws: WebSocket) -> None:
        await ws.accept()
        conversation_id = str(uuid.uuid4())
        session: AgentSession | None = None
        try:
            session = await session_factory(conversation_id)
        except Exception as exc:  # pragma: no cover - 装配失败直接通知客户端
            await ws.send_json({"type": ERROR, "message": f"会话初始化失败: {exc}"})
            await ws.close()
            return

        # None 作为发送循环的终止哨兵
        queue: asyncio.Queue[AgentEvent | ProtocolEvent | None] = asyncio.Queue()
        agent_task: asyncio.Task | None = None

        async def run_agent(text: str) -> None:
            """消费 AgentSession 事件流并放入发送队列。"""
            if session is None:
                return
            try:
                async for event in session.submit(text):
                    await queue.put(event)
            except Exception as exc:  # pragma: no cover - 兜底
                await queue.put(ProtocolEvent(type=ERROR, payload={"message": f"Agent 运行异常: {exc}"}))
            finally:
                usage = session.service.usage
                await queue.put(
                    ProtocolEvent(
                        type=TOKEN_USAGE,
                        payload={
                            "total": usage.total_tokens,
                            "input": usage.prompt_tokens,
                            "output": usage.completion_tokens,
                        },
                    )
                )

        async def send_loop() -> None:
            while True:
                event = await queue.get()
                if event is None:
                    break
                if isinstance(event, ProtocolEvent):
                    data = {"type": event.type, **event.payload}
                else:
                    proto = to_protocol(event)
                    if proto is None:
                        continue
                    data = {"type": proto.type, **proto.payload}
                await ws.send_json(data)

        async def recv_loop() -> None:
            nonlocal agent_task, session
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = data.get("type")
                if mtype == "user_message":
                    text = str(data.get("text") or "").strip()
                    if not text:
                        continue
                    if agent_task is not None and not agent_task.done():
                        await queue.put(
                            ProtocolEvent(type=NOTICE, payload={"message": "正在处理中，请稍候"})
                        )
                        continue
                    agent_task = asyncio.create_task(run_agent(text))
                elif mtype == "stop":
                    if session is None:
                        continue
                    session.interrupt()
                elif mtype == "confirm_response":
                    if session is None:
                        continue
                    await session.loop.resolve_confirmation(
                        str(data.get("call_id", "")),
                        allowed=bool(data.get("allowed", False)),
                    )
                elif mtype == "ping":
                    await queue.put(ProtocolEvent(type="pong", payload={}))
                elif mtype == "get_token_usage":
                    if session is None:
                        continue
                    usage = session.service.usage
                    await queue.put(
                        ProtocolEvent(
                            type=TOKEN_USAGE,
                            payload={
                                "total": usage.total_tokens,
                                "input": usage.prompt_tokens,
                                "output": usage.completion_tokens,
                            },
                        )
                    )
                elif mtype == "get_emotion_state":
                    if session is None:
                        continue
                    state = session.loop.emotion_engine.current_state if session.loop.emotion_engine else None
                    await queue.put(
                        ProtocolEvent(
                            type="emotion_state_changed",
                            payload={
                                "dominant": state.dominant if state else "neutral",
                                "scores": state.scores if state else {},
                            },
                        )
                    )
                elif mtype == "list_sessions":
                    store = _get_or_create_session_store()
                    items = store.list_all()
                    await queue.put(
                        ProtocolEvent(
                            type="session_list",
                            payload={
                                "sessions": [
                                    {
                                        "id": s.id,
                                        "title": s.title_or_default,
                                        "updated_at": s.updated_at,
                                        "message_count": s.message_count,
                                        "pinned": s.pinned,
                                    }
                                    for s in items
                                ]
                            },
                        )
                    )
                elif mtype == "switch_session":
                    target_id = str(data.get("session_id", ""))
                    if not target_id:
                        continue
                    # 切换会话需要创建新的 AgentSession
                    try:
                        new_session = await session_factory(target_id)
                        session = new_session
                        await queue.put(
                            ProtocolEvent(
                                type="session_switched",
                                payload={"session_id": target_id},
                            )
                        )
                    except Exception as exc:
                        await queue.put(
                            ProtocolEvent(
                                type=ERROR,
                                payload={"message": f"切换会话失败: {exc}"},
                            )
                        )
                elif mtype == "delete_session":
                    target_id = str(data.get("session_id", ""))
                    if not target_id:
                        continue
                    store = _get_or_create_session_store()
                    deleted = store.delete(target_id)
                    await queue.put(
                        ProtocolEvent(
                            type="session_deleted",
                            payload={"session_id": target_id, "deleted": deleted},
                        )
                    )
                elif mtype == "close":
                    break

        try:
            await asyncio.gather(send_loop(), recv_loop())
        except WebSocketDisconnect:
            pass
        finally:
            if agent_task is not None:
                agent_task.cancel()
            queue.put_nowait(None)
            if session is not None:
                await session.service.aclose()

    return router
