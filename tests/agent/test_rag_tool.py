"""测试 RAG 检索工具 search_knowledge。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.knowledge import get_knowledge_store, reset_knowledge_store
from agent.tools.base import ToolContext
from agent.tools.rag import search_knowledge
from core.vector.config import EmbeddingConfig, VectorConfig


@pytest.fixture
def knowledge(fake_embedding):
    reset_knowledge_store()
    with patch(
        "agent.knowledge.store.EmbeddingFactory.create", return_value=fake_embedding
    ):
        store = get_knowledge_store(
            VectorConfig(
                storage="memory",
                similarity_threshold=0.0,
                embedding=EmbeddingConfig(model="m", url="http://localhost:1"),
            )
        )
        yield store
    reset_knowledge_store()


def _ctx() -> ToolContext:
    return ToolContext(user_query="测试", conversation_id="c1", memory=None)


async def test_search_knowledge_returns_fragment(knowledge):
    await knowledge.index_document("doc1", "测试文档", ["Aliya 喜欢星星和咖啡"])
    result = await search_knowledge(_ctx(), {"query": "Aliya 喜欢星星"})
    assert "测试文档" in result
    assert "Aliya" in result


async def test_search_knowledge_empty_safe(knowledge):
    assert knowledge is not None  # fixture 就绪（空库隔离）
    result = await search_knowledge(_ctx(), {"query": "完全不存在的主题"})
    assert result == "（知识库无相关片段）"


async def test_search_knowledge_top_k_param(knowledge):
    await knowledge.index_document("doc1", "文档", [f"关键词主题{i}" for i in range(10)])
    result = await search_knowledge(_ctx(), {"query": "关键词主题", "top_k": 3})
    assert "（知识库无相关片段）" not in result
    # 返回的片段按 [1] [2] [3] 编号，不超 3 条
    assert result.count("来自《文档》") == 3


async def test_search_knowledge_top_k_clamped(knowledge):
    """top_k 超限值被 clamp，避免模型传超大值导致召回过量。"""
    await knowledge.index_document("doc1", "文档", [f"关键词主题{i}" for i in range(30)])
    result = await search_knowledge(_ctx(), {"query": "关键词主题", "top_k": 9999})
    assert result.count("来自《文档》") == 20  # clamp 上限 20
    result_low = await search_knowledge(_ctx(), {"query": "关键词主题", "top_k": -5})
    assert result_low.count("来自《文档》") == 1  # clamp 下限 1


async def test_search_knowledge_empty_query_safe(knowledge):
    assert knowledge is not None  # fixture 就绪（空库隔离）
    result = await search_knowledge(_ctx(), {"query": "   "})
    assert result == "（知识库无相关片段）"
