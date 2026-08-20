"""测试：build_agent_session / build_session_factory 装配工厂。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.loop import AgentLoop
from agent.session import AgentSession, build_agent_session, build_session_factory


async def _noop_shared_init(*_args, **_kwargs) -> None:
    """桩掉一次性共享初始化（知识库索引 + MCP 同步），避免测试访问外部依赖。"""
    return None


@pytest.fixture
def mock_assembly(mocker):
    """桩掉真实外部依赖：LLM service、GRAG 记忆、共享初始化；注册表走真实内置工具。"""
    service = MagicMock()
    service.provider = MagicMock()
    service.conversation_id = "test-cid"
    mocker.patch("core.llm.create_from_config", return_value=service)
    mocker.patch(
        "core.memory.memory_manager.GRAGMemoryManager",
        side_effect=RuntimeError("no neo4j"),
    )
    mocker.patch("agent.session._ensure_shared_initialized", new=_noop_shared_init)
    return service


@pytest.mark.asyncio
async def test_build_agent_session_returns_agent_session(mock_assembly):
    session = await build_agent_session("test-cid")
    assert isinstance(session, AgentSession)
    assert isinstance(session.loop, AgentLoop)
    assert session.conversation_id == "test-cid"
    assert session.service is mock_assembly


@pytest.mark.asyncio
async def test_build_agent_session_wires_emotion_engine(mock_assembly):
    session = await build_agent_session("test-cid")
    assert session.loop.emotion_engine is not None
    assert session.service is mock_assembly


@pytest.mark.asyncio
async def test_build_agent_session_graceful_without_memory(mock_assembly):
    """GRAG 记忆不可用时装配不中断（降级 memory=None）。"""
    session = await build_agent_session("test-cid")
    assert session.loop.memory is None
    assert session.service is mock_assembly


@pytest.mark.asyncio
async def test_build_session_factory_returns_factory(mock_assembly):
    factory = build_session_factory()
    assert callable(factory)
    session = await factory("test-cid")
    assert isinstance(session, AgentSession)
    assert session.service is mock_assembly
