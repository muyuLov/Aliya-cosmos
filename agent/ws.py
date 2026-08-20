"""WS 网关：AG-UI 风格事件流收发，/agent/ws 端点

职责：
- 接收循环：客户端消息分发（user_message / stop / confirm_response / ping）
- 发送循环：队列事件序列化为线上协议 JSON 推送给客户端
- 连接级会话：经 SessionManager 获取/创建 AgentSession
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agent.events import (
    ERROR,
    NOTICE,
    TOKEN_USAGE,
    AgentEvent,
    ProtocolEvent,
    to_protocol,
)
from agent.session import AgentSession

SessionFactory = Callable[[str], Awaitable[AgentSession]]


def create_ws_router(session_factory: SessionFactory | None = None) -> APIRouter:
    """创建 WS 路由；session_factory 可注入（测试用 mock，生产用默认装配）。"""
    router = APIRouter()

    if session_factory is None:
        session_factory = _default_session_factory()

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
            nonlocal agent_task
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
                    session.interrupt()
                elif mtype == "confirm_response":
                    await session.loop.resolve_confirmation(
                        str(data.get("call_id", "")),
                        allowed=bool(data.get("allowed", False)),
                    )
                elif mtype == "ping":
                    await queue.put(ProtocolEvent(type="pong", payload={}))
                elif mtype == "get_token_usage":
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
                    pass  # M1 无情绪引擎，返回空
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
            await session.service.aclose()

    return router


def _default_session_factory() -> SessionFactory:
    """生产装配：真实 LLM 服务 + GRAG 记忆 + 内置工具。"""

    async def factory(conversation_id: str) -> AgentSession:
        from agent.context import ContextBuilder
        from agent.loop import AgentLoop
        from agent.tools import PermissionChecker, ToolRegistry
        from agent.tools.builtin import register_builtin_tools
        from core.config import get_config_instance
        from core.llm import create_from_config
        from core.logger import get_logger
        from core.memory.memory_manager import GRAGMemoryManager

        logger = get_logger(__name__)
        cfg = get_config_instance("data/config/main.yml")

        service = create_from_config("data/config/main.yml", conversation_id=conversation_id)

        memory = None
        try:
            memory = GRAGMemoryManager()
        except Exception as exc:  # pragma: no cover - 记忆不可用不阻塞
            logger.warning("GRAG 记忆初始化失败，工具将降级: %s", exc)

        registry = ToolRegistry()
        register_builtin_tools(registry)

        perm_path = cfg.get(
            "cosmos.service.agent.permissions.config_path", "data/config/Permissions.yml"
        )
        checker = PermissionChecker(perm_path)

        loop = AgentLoop(
            service=service,
            registry=registry,
            checker=checker,
            context=ContextBuilder(),
            memory=memory,
        )
        return AgentSession(conversation_id, service, loop)

    return factory
