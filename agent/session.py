"""会话生命周期与装配：AgentSession（单会话封装）+ 共享单例装配

参考 claude-code QueryEngine 模式：一个对话线程对应一个 AgentSession 实例，
跨轮持有 ConversationService、usage 累计与中断控制。
会话装配统一收敛到本模块（build_agent_session），WS / 飞书 / 微信共用；
AgentSession 支持 EventSink 注册，进程内事件可被外部渠道订阅。
共享单例（工具注册表、会话元数据）均在此处惰性创建与复用。
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable

from agent.events import AgentEvent, EventSink, ProtocolEvent
from agent.loop import AgentLoop
from core.llm.models import TokenUsage
from core.logger import get_logger

logger = get_logger(__name__)

SessionFactory = Callable[[str], Awaitable["AgentSession"]]


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
        self._sinks: list[EventSink] = []

    @property
    def service(self):
        return self._service

    @property
    def loop(self) -> AgentLoop:
        return self._loop

    def add_sink(self, sink: EventSink) -> None:
        """注册事件订阅者（如飞书/微信渠道）。"""
        self._sinks.append(sink)

    def remove_sink(self, sink: EventSink) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    async def _emit_to_sinks(self, event: AgentEvent | ProtocolEvent) -> None:
        # 进程内事件（AgentEvent）与线上协议事件（ProtocolEvent，含 CONFIRM_REQUEST）
        # 均广播给渠道；sink 自行挑选关心类型，异常隔离不阻断主流程。
        for sink in self._sinks:
            try:
                await sink.emit(event)
            except Exception as exc:  # pragma: no cover - sink 故障不阻断主流程
                logger.warning("EventSink.emit 失败（已隔离）: %s", exc)

    async def submit(self, text: str, images: list[str] | None = None) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        async for event in self._loop.submit_user_message(text, images):
            await self._emit_to_sinks(event)  # 全部事件广播给渠道 sink
            yield event

    def interrupt(self) -> None:
        self._loop.interrupt()

    def reset_abort(self) -> None:
        self._loop.reset_abort()


# ── 共享装配状态（跨会话复用：知识库索引 + MCP 同步）─────────────────────────
_shared_registry: Any = None
_init_done = False
_connected_servers: list[str] = []
_init_lock = asyncio.Lock()


def _get_or_create_registry() -> Any:
    """获取共享内置工具注册表（内置 + RAG + Skill + MCP），跨会话复用。"""
    global _shared_registry
    if _shared_registry is None:
        # asyncio 单线程下同步创建无 await 间隙，不会并发交错，无需加锁
        from agent.tools import ToolRegistry
        from agent.tools.builtin import register_builtin_tools

        registry = ToolRegistry()
        register_builtin_tools(registry)
        _shared_registry = registry
    return _shared_registry


_session_store: Any = None


def _get_or_create_session_store() -> Any:
    """进程内共享的会话元数据存储器（与工具注册表单例同模式）。"""
    global _session_store
    if _session_store is None:
        from agent.session_store import SessionStore

        _session_store = SessionStore()
    return _session_store


async def _ensure_shared_initialized(registry: Any, cfg: Any) -> None:
    """启动一次性：索引知识库目录 + 同步 MCP 服务器（各自失败隔离，互不阻断）。"""
    global _init_done, _connected_servers
    async with _init_lock:
        if _init_done:
            return
        from agent.knowledge import index_knowledge_directory
        from agent.mcp import load_mcp_specs, sync_mcp_servers

        try:
            await index_knowledge_directory(
                cfg.get("cosmos.service.agent.knowledge.dir", "data/knowledge")
            )
        except Exception as exc:  # pragma: no cover - 索引失败 RAG 降级为空库
            logger.warning("知识库索引失败（RAG 降级为空库）: %s", exc)
        try:
            _, connected = await sync_mcp_servers(registry, load_mcp_specs())
            _connected_servers = connected
        except Exception as exc:  # pragma: no cover - MCP 整体失败不阻断启动
            logger.warning("MCP 同步失败（跳过）: %s", exc)
        _init_done = True


async def build_agent_session(conversation_id: str) -> AgentSession:
    """生产装配：真实 LLM 服务 + GRAG 记忆 + 共享工具注册表 + 情绪引擎。

    从 agent/ws.py 的 _default_session_factory 迁移，行为完全一致：
    知识库索引 / MCP 同步在此处一次性 await，跨会话复用。
    """
    from agent.context import ContextBuilder
    from agent.emotion.engine import create_emotion_engine
    from agent.loop import AgentLoop
    from agent.tools import PermissionChecker
    from core.config import get_config_instance
    from core.llm import create_from_config
    from core.memory.memory_manager import GRAGMemoryManager

    cfg = get_config_instance("data/config/main.yml")
    service = create_from_config("data/config/main.yml", conversation_id=conversation_id)

    memory = None
    try:
        memory = GRAGMemoryManager()
    except Exception as exc:  # pragma: no cover - 记忆不可用不阻塞
        logger.warning("GRAG 记忆初始化失败，工具将降级: %s", exc)

    registry = _get_or_create_registry()
    builder = ContextBuilder()
    await _ensure_shared_initialized(registry, cfg)
    builder.available_mcp_servers = list(_connected_servers)

    perm_path = cfg.get(
        "cosmos.service.agent.permissions.config_path", "data/config/Permissions.yml"
    )
    checker = PermissionChecker(perm_path)

    emotion_engine = create_emotion_engine(service.provider)

    loop = AgentLoop(
        service=service,
        registry=registry,
        checker=checker,
        context=builder,
        memory=memory,
        emotion_engine=emotion_engine,
    )
    return AgentSession(conversation_id, service, loop)


def build_session_factory() -> SessionFactory:
    """返回兼容 WS 的 SessionFactory（无参工厂闭包）。"""

    async def factory(cid: str) -> AgentSession:
        return await build_agent_session(cid)

    return factory
