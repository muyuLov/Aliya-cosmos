"""测试 agent.knowledge.store：KnowledgeStore 封装 VectorStore + EmbeddingFactory。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.knowledge.store import KnowledgeStore
from core.vector.config import EmbeddingConfig, VectorConfig


def _cfg() -> VectorConfig:
    return VectorConfig(
        storage="memory",
        similarity_threshold=0.0,
        embedding=EmbeddingConfig(model="m", url="http://localhost:1"),
    )


@pytest.fixture
def store(fake_embedding):
    with patch(
        "agent.knowledge.store.EmbeddingFactory.create", return_value=fake_embedding
    ):
        yield KnowledgeStore(_cfg())


async def test_index_document_returns_ids(store):
    ids = await store.index_document("doc1", "标题", ["片段一", "片段二"])
    assert len(ids) == 2
    assert all(isinstance(i, str) and i for i in ids)


async def test_search_finds_indexed_chunks(store):
    await store.index_document("doc1", "测试文档", ["Aliya 喜欢星星", "Aliya 喜欢咖啡"])
    results = await store.search("Aliya 喜欢咖啡", top_k=5, threshold=0.0)
    assert results
    assert results[0].text == "Aliya 喜欢咖啡"
    assert results[0].metadata["title"] == "测试文档"


async def test_search_empty_store(store):
    assert await store.search("任何查询", top_k=5) == []


async def test_aclose_no_raise(store):
    await store.aclose()
