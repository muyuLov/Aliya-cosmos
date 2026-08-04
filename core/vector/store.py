"""向量存储与检索模块

提供基于余弦相似度的内存向量库：
- 内存存储（进程内数据，进程退出即清空）
- 增删查、批量添加、top-k 检索、阈值过滤
- 向量维度一致性校验
- 线程安全（读改写均加锁）
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.logger import get_logger
from core.vector.config import VectorConfig, get_vector_config
from core.vector.embedding import EmbeddingFactory, EmbeddingProvider
from core.vector.exceptions import (
    DimensionMismatchError,
    StoreError,
    VectorNotEnabledError,
)

logger = get_logger(__name__)


def _batch_cosine(
    vectors: Sequence[List[float]], query_vector: List[float]
) -> List[float]:
    """批量计算多个归一化向量与查询向量的余弦相似度。

    使用 numpy 向量化点积替代逐条 Python 循环，条目较多时性能提升显著
    （向量已归一化，点积即余弦相似度）。

    维度不一致或输入为空时返回全 0，与 :meth:`VectorStore._cosine` 语义一致。
    """
    if not vectors:
        return []
    matrix = np.asarray(vectors, dtype=np.float32)
    query = np.asarray(query_vector, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != query.shape[0]:
        return [0.0] * len(vectors)
    return (matrix @ query).tolist()


@dataclass
class VectorItem:
    """向量存储条目"""

    id: str
    text: str
    vector: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class SearchResult:
    """检索结果"""

    id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """内存向量存储与检索引擎

    向量仅保存在进程内存中，进程退出即清空；
    如需持久化，由上层负责（如定期导出或接入外部向量数据库）。

    Args:
        embedding: 向量化提供者。
        config:    向量模块配置（相似度阈值、top_k 等）。
    """

    def __init__(self, embedding: EmbeddingProvider, config: VectorConfig) -> None:
        self._embedding = embedding
        self._config = config
        self._items: Dict[str, VectorItem] = {}
        self._dimension: Optional[int] = embedding.dimension or None
        self._lock = threading.RLock()

    # ── 基础信息 ────────────────────────────────────────────

    @property
    def embedding(self) -> EmbeddingProvider:
        """当前向量化提供者"""
        return self._embedding

    @property
    def dimension(self) -> int:
        """已入库向量的维度；尚无数据时返回 0。"""
        return self._dimension or 0

    @property
    def count(self) -> int:
        """当前条目数"""
        with self._lock:
            return len(self._items)

    # ── 写入 ────────────────────────────────────────────────

    async def add(
        self,
        text: str,
        metadata: Dict[str, Any] | None = None,
        item_id: str | None = None,
    ) -> str:
        """向量化并添加一条文本。

        Args:
            text:     待存储的文本。
            metadata: 附加元数据（可选）。
            item_id:  自定义条目 ID，缺省自动生成。

        Returns:
            条目 ID。

        Raises:
            DimensionMismatchError: 与已有条目维度不一致。
            StoreError: 条目 ID 已存在或文本为空白。
        """
        if not text.strip():
            raise StoreError("文本为空白，拒绝添加")
        vector = (await self._embedding.embed([text]))[0]
        return self._add_with_vector(text, vector, metadata=metadata, item_id=item_id)

    async def add_many(
        self,
        items: Sequence[Dict[str, Any]],
    ) -> List[str]:
        """批量向量化并添加多条文本（原子操作：任一校验失败则不写入任何条目）。

        Args:
            items: 条目字典列表，每个元素支持 ``text`` / ``metadata`` / ``id`` 字段。

        Returns:
            条目 ID 列表（与输入顺序一致）。

        Raises:
            DimensionMismatchError: 与已有条目维度不一致。
            StoreError: 条目 ID 已存在或文本为空白。
        """
        if not items:
            return []
        texts = [str(it.get("text", "")) for it in items]
        for index, text in enumerate(texts, start=1):
            if not text.strip():
                raise StoreError(f"第 {index} 条文本为空白，拒绝添加")

        vectors = await self._embedding.embed(texts)

        with self._lock:
            # 阶段一：整体校验维度与 ID 冲突，不修改任何状态
            ids: List[str] = []
            for item, vector in zip(items, vectors):
                if self._dimension is not None and len(vector) != self._dimension:
                    raise DimensionMismatchError(self._dimension, len(vector))
                iid = item.get("id") or uuid.uuid4().hex
                if iid in self._items or iid in ids:
                    raise StoreError(f"已存在相同 ID 的条目: {iid}")
                ids.append(iid)
            # 阶段二：全部校验通过后统一确立维度并写入
            if self._dimension is None and vectors:
                self._dimension = len(vectors[0])
            for item, vector, iid in zip(items, vectors, ids):
                self._items[iid] = VectorItem(
                    id=iid,
                    text=str(item.get("text", "")),
                    vector=vector,
                    metadata=dict(item.get("metadata") or {}),
                )
        return ids

    def _add_with_vector(
        self,
        text: str,
        vector: List[float],
        metadata: Dict[str, Any] | None,
        item_id: str | None,
    ) -> str:
        with self._lock:
            self._check_dimension(vector)
            iid = item_id or uuid.uuid4().hex
            if iid in self._items:
                raise StoreError(f"已存在相同 ID 的条目: {iid}")
            self._items[iid] = VectorItem(
                id=iid,
                text=text,
                vector=vector,
                metadata=dict(metadata or {}),
            )
        return iid

    def _check_dimension(self, vector: List[float]) -> None:
        """校验向量维度：以已知维度为基准（配置指定或首个入库向量确立），不一致抛错。"""
        if self._dimension is None:
            self._dimension = len(vector)
        elif len(vector) != self._dimension:
            raise DimensionMismatchError(self._dimension, len(vector))

    # ── 读取与检索 ──────────────────────────────────────────

    def get(self, item_id: str) -> VectorItem | None:
        """按 ID 获取条目，不存在时返回 None。"""
        with self._lock:
            return self._items.get(item_id)

    def search(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> List[SearchResult]:
        """同步检索与 query 最相似的条目（按余弦相似度排序）。

        内部通过 ``asyncio.run`` 驱动向量化，调用方须在无运行中事件循环的
        上下文使用；异步场景请使用 :meth:`search_async`。

        Args:
            query:     查询文本。
            top_k:     返回条数上限，缺省使用配置值。
            threshold: 相似度阈值（0-1），低于阈值的条目被过滤，缺省使用配置值。

        Returns:
            按相似度降序排列的检索结果列表。
        """
        return asyncio.run(self.search_async(query, top_k=top_k, threshold=threshold))

    async def search_async(
        self,
        query: str,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> List[SearchResult]:
        """异步检索与 query 最相似的条目（按余弦相似度排序）。

        Args:
            query:     查询文本。
            top_k:     返回条数上限，缺省使用配置值。
            threshold: 相似度阈值（0-1），低于阈值的条目被过滤，缺省使用配置值。

        Returns:
            按相似度降序排列的检索结果列表。
        """
        if not query:
            return []

        top_k = top_k or self._config.top_k
        threshold = (
            self._config.similarity_threshold if threshold is None else threshold
        )

        query_vector = (await self._embedding.embed([query]))[0]
        # 查询向量维度与已知维度（配置期望或已入库条目确立）不一致时尽早失败
        if self._dimension is not None and len(query_vector) != self._dimension:
            raise DimensionMismatchError(self._dimension, len(query_vector))

        # 锁内仅拷贝快照，余弦计算放到锁外（numpy 批量），缩短持锁时间
        with self._lock:
            snapshot = list(self._items.values())
        if not snapshot:
            return []

        scores = _batch_cosine([item.vector for item in snapshot], query_vector)
        ranked = [
            (item, score)
            for item, score in zip(snapshot, scores)
            if score >= threshold
        ]
        ranked.sort(key=lambda pair: pair[1], reverse=True)

        return [
            SearchResult(
                id=item.id,
                text=item.text,
                score=score,
                metadata=dict(item.metadata),
            )
            for item, score in ranked[:top_k]
        ]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        """计算两个向量的余弦相似度。

        向量已由 embedding 归一化，故点积即余弦相似度。
        维度不一致时返回 0.0（无法计算相似度，不做静默截断）。
        """
        if len(a) != len(b):
            return 0.0
        if not a:
            return 0.0
        dot = 0.0
        for i in range(len(a)):
            dot += a[i] * b[i]
        return dot

    # ── 删除与清理 ──────────────────────────────────────────

    def delete(self, item_id: str) -> bool:
        """删除指定 ID 的条目。

        Returns:
            删除成功返回 True，条目不存在返回 False。
        """
        with self._lock:
            if item_id not in self._items:
                return False
            del self._items[item_id]
            return True

    def clear(self) -> None:
        """清空全部条目并重置维度。"""
        with self._lock:
            self._items.clear()
            self._dimension = None

    async def aclose(self) -> None:
        """关闭底层向量化提供者资源（如 API 客户端连接池）。"""
        aclose = getattr(self._embedding, "aclose", None)
        if aclose is not None:
            await aclose()


# ── 全局单例 ────────────────────────────────────────────────

_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_vector_store() -> VectorStore:
    """获取向量存储单例（线程安全懒加载）。

    Raises:
        VectorNotEnabledError: 向量模块未启用时抛出。
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                config = get_vector_config()
                if not config.enabled:
                    raise VectorNotEnabledError()
                _store = VectorStore(
                    embedding=EmbeddingFactory.create(config),
                    config=config,
                )
                logger.info(
                    "向量存储初始化完成: provider=%s, dim=%d, count=%d",
                    _store.embedding.provider_name,
                    _store.dimension,
                    _store.count,
                )
    return _store


def reset_vector_store() -> None:
    """重置向量存储单例（主要用于测试或配置热重载）。

    仅丢弃引用，不关闭底层资源；如需释放 embedding API 客户端，
    请使用 :func:`shutdown_vector_store`。
    """
    global _store
    with _store_lock:
        _store = None
        logger.debug("向量存储单例已重置")


async def shutdown_vector_store() -> None:
    """关闭并重置向量存储单例，释放底层资源（如 API 客户端连接池）。

    与 ``reset_vector_store`` 的区别：会异步调用底层 embedding 的 ``aclose()``，
    用于应用退出或配置热重载前的资源清理。
    """
    global _store
    store = None
    with _store_lock:
        store = _store
        _store = None
    if store is not None:
        await store.aclose()
        logger.info("向量存储已关闭")


__all__ = [
    "VectorItem",
    "SearchResult",
    "VectorStore",
    "get_vector_store",
    "reset_vector_store",
    "shutdown_vector_store",
]
