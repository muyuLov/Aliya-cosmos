"""RAG 知识库：封装 core/vector，提供文档索引与片段检索。"""

from __future__ import annotations

from core.logger import get_logger
from core.vector.config import VectorConfig, get_vector_config
from core.vector.embedding import EmbeddingFactory
from core.vector.store import SearchResult, VectorStore

logger = get_logger(__name__)


class KnowledgeStore:
    """单例知识库：持有 VectorStore 与 embedding 提供者。

    文档以「片段（chunk）」为单位切分后入库；检索返回 top-k 片段。
    """

    def __init__(self, config: VectorConfig | None = None) -> None:
        # 真实接口：EmbeddingFactory.create(config: VectorConfig) 必传 config；
        # config 缺 embedding.model/url 时 OpenAIEmbeddingProvider 直接抛 VectorConfigError（无静默降级）。
        cfg = config or get_vector_config()
        embedding = EmbeddingFactory.create(cfg)
        self._store = VectorStore(embedding, cfg)

    async def index_document(
        self,
        doc_id: str,
        title: str,
        chunks: list[str],
    ) -> list[str]:
        """将一篇文档的若干片段入库，返回片段条目 ID 列表。"""
        items = [
            {"text": c, "metadata": {"doc_id": doc_id, "title": title}}
            for c in chunks
        ]
        return await self._store.add_many(items)

    async def search(
        self, query: str, top_k: int = 5, threshold: float | None = None
    ) -> list[SearchResult]:
        """检索与 query 最相关的片段（低于阈值被过滤）。"""
        return await self._store.search_async(query, top_k=top_k, threshold=threshold)

    async def aclose(self) -> None:
        await self._store.aclose()
