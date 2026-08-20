"""知识库 / Skill / MCP 端到端冒烟：RAG / Skill / MCP 三类工具在同一注册表闭环。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent.knowledge import get_knowledge_store, reset_knowledge_store
from agent.tools import ToolContext, ToolRegistry
from agent.tools.builtin import register_builtin_tools
from core.llm.cache import ContextCache
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService
from core.vector.config import EmbeddingConfig, VectorConfig


def _cfg() -> VectorConfig:
    return VectorConfig(
        storage="memory",
        similarity_threshold=0.0,
        embedding=EmbeddingConfig(model="m", url="http://localhost:1"),
    )


def _init_store(fake_embedding):
    reset_knowledge_store()
    with patch(
        "agent.knowledge.store.EmbeddingFactory.create", return_value=fake_embedding
    ):
        return get_knowledge_store(_cfg())


async def test_build_tools_schema_has_all_three(fake_embedding):
    _init_store(fake_embedding)
    reg = ToolRegistry()
    register_builtin_tools(reg)
    names = {t["function"]["name"] for t in reg.build_tools_schema()}
    assert "search_knowledge" in names
    assert "roll_dice" in names
    reset_knowledge_store()


async def test_search_knowledge_via_registry(fake_embedding):
    store = _init_store(fake_embedding)
    await store.index_document("doc1", "测试文档", ["Aliya 喜欢星星"])
    reg = ToolRegistry()
    register_builtin_tools(reg)
    result = await reg.execute(
        "search_knowledge", ToolContext("问题", "c1"), {"query": "Aliya 喜欢星星"}
    )
    assert "测试文档" in result
    reset_knowledge_store()


async def test_roll_dice_via_registry():
    reg = ToolRegistry()
    register_builtin_tools(reg)
    result = await reg.execute(
        "roll_dice", ToolContext("掷骰", "c1"), {"sides": 6, "count": 1}
    )
    assert result.startswith("掷出：")


async def test_loop_dispatches_search_knowledge(fake_embedding):
    """AgentLoop._tool_phase 能正常调度 search_knowledge（复用现有夹具风格）。"""
    from agent.context import ContextBuilder
    from agent.events import ToolCallResult
    from agent.loop import AgentLoop
    from agent.tools import PermissionChecker
    from agent.tools.registry import ToolRegistry

    store = _init_store(fake_embedding)
    await store.index_document("doc1", "测试文档", ["Aliya 喜欢星星"])

    reg = ToolRegistry()
    register_builtin_tools(reg)

    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.supports_thinking = True
    calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_knowledge",
                "arguments": '{"query": "Aliya 喜欢星星"}',
            },
        }
    ]
    responses = [
        ChatResponse(content="", finish_reason="tool_calls", tool_calls=calls),
        ChatResponse(
            content="最终回答",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ),
    ]
    provider.async_chat_completion = AsyncMock(side_effect=responses)
    provider.last_stream_usage = TokenUsage(
        prompt_tokens=10, completion_tokens=5, total_tokens=15
    )

    async def fake_stream(_request):
        yield "好"

    provider.stream_chat_completion = fake_stream
    provider.aclose = AsyncMock()

    service = ConversationService(
        provider=provider,
        cache=ContextCache(ttl=3600),
        conversation_id="test-session",
    )
    checker = PermissionChecker("data/config/Permissions.yml")
    loop = AgentLoop(
        service=service,
        registry=reg,
        checker=checker,
        context=ContextBuilder("data/prompts"),
    )
    results = []
    async for ev in loop.submit_user_message("Aliya 喜欢什么"):
        if isinstance(ev, ToolCallResult):
            results.append(ev)
    assert results
    assert "测试文档" in results[0].output
    await service.aclose()
    reset_knowledge_store()
