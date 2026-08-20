"""会话生命周期：AgentSession（单会话封装）+ SessionManager（多会话注册表）

参考 claude-code QueryEngine 模式：一个对话线程对应一个 AgentSession 实例，
跨轮持有 ConversationService、usage 累计与中断控制。
"""

from __future__ import annotations

from typing import AsyncGenerator, Callable

from agent.events import AgentEvent, ProtocolEvent
from agent.loop import AgentLoop
from core.llm.models import TokenUsage


class AgentSession:
    """一个对话线程对应一个实例，持有 AgentLoop + ConversationService。"""

    def __init__(
        self,
        conversation_id: str,
        service,
        loop: AgentLoop,
    ) -> None:
        self.conversation_id = conversation_id
        self._service = service
        self._loop = loop
        self.usage = TokenUsage()

    @property
    def service(self):
        return self._service

    @property
    def loop(self) -> AgentLoop:
        return self._loop

    async def submit(self, text: str) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        async for event in self._loop.submit_user_message(text):
            yield event

    def interrupt(self) -> None:
        self._loop.interrupt()

    def reset_abort(self) -> None:
        self._loop.reset_abort()


class SessionManager:
    """会话注册表：conversation_id → AgentSession（M1 内存态，M2 持久化）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get_or_create(self, conversation_id: str, factory: Callable[[], AgentSession]) -> AgentSession:
        session = self._sessions.get(conversation_id)
        if session is None:
            session = factory()
            self._sessions[conversation_id] = session
        return session

    def get(self, conversation_id: str) -> AgentSession | None:
        return self._sessions.get(conversation_id)

    def remove(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)

    def close_all(self) -> None:
        """关闭并清空全部会话（调用方负责 await service.aclose()）。"""
        self._sessions.clear()
