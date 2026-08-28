"""core.vector - 向量模块

向量化与向量检索能力（内存计算）：

- ``embedding.py``: 向量化提供者（OpenAI 兼容 Embedding API）
- ``store.py``:     向量存储与检索（内存存储，余弦相似度）
- ``config.py``:    配置加载（``cosmos.service.vector``）
- ``exceptions.py``: 异常定义（错误码 ``VEC_xxx``）

快速使用：

.. code-block:: python

    from core.vector import add, search_async

    await add("我喜欢喝咖啡，也爱熬夜写代码")
    results = await search_async("我的爱好是什么")
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.vector.config import (
    EmbeddingConfig,
    VectorConfig,
    get_vector_config,
    reload_config,
)
from core.vector.embedding import (
    EmbeddingFactory,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from core.vector.exceptions import (
    DimensionMismatchError,
    EmbeddingAPIError,
    EmbeddingError,
    StoreError,
    VectorConfigError,
    VectorError,
    VectorNotEnabledError,
)
from core.vector.store import (
    SearchResult,
    VectorItem,
    VectorStore,
    get_vector_store,
    reset_vector_store,
    shutdown_vector_store,
)

__all__ = [
    # 存储与检索
    "VectorStore",
    "VectorItem",
    "SearchResult",
    "get_vector_store",
    "reset_vector_store",
    "shutdown_vector_store",
    # 向量化
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "EmbeddingFactory",
    # 配置
    "VectorConfig",
    "EmbeddingConfig",
    "get_vector_config",
    "reload_config",
    # 异常
    "VectorError",
    "VectorConfigError",
    "VectorNotEnabledError",
    "EmbeddingError",
    "EmbeddingAPIError",
    "DimensionMismatchError",
    "StoreError",
]


# ── 便捷接口（委托全局单例） ──────────────────────────────


async def add(
    text: str,
    metadata: Dict[str, Any] | None = None,
    item_id: str | None = None,
) -> str:
    """向量化并存储一条文本，返回条目 ID。"""
    return await get_vector_store().add(text, metadata=metadata, item_id=item_id)


async def add_many(items: List[Dict[str, Any]]) -> List[str]:
    """批量向量化并存储多条文本，返回条目 ID 列表。"""
    return await get_vector_store().add_many(items)


def search(
    query: str,
    top_k: int | None = None,
    threshold: float | None = None,
) -> List[SearchResult]:
    """同步检索与 query 最相似的条目（需在无运行中事件循环的上下文使用）。"""
    return get_vector_store().search(query, top_k=top_k, threshold=threshold)


async def search_async(
    query: str,
    top_k: int | None = None,
    threshold: float | None = None,
) -> List[SearchResult]:
    """异步检索与 query 最相似的条目。"""
    return await get_vector_store().search_async(
        query, top_k=top_k, threshold=threshold
    )


def delete(item_id: str) -> bool:
    """删除指定 ID 的条目。"""
    return get_vector_store().delete(item_id)


def clear() -> None:
    """清空全部条目。"""
    get_vector_store().clear()


def count() -> int:
    """当前条目数。"""
    return get_vector_store().count
