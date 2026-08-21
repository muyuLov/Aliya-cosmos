"""向量存储与检索模块

提供基于余弦相似度的向量库（内存计算 + 可选 Milvus 持久化）：
- 内存存储（进程内数据，检索逻辑）
- 增删查、批量添加、top-k 检索、阈值过滤
- 向量维度一致性校验
- 线程安全（读改写均加锁）
- Milvus 持久化后端（storage=milvus 时启用）：跨会话保存与恢复，
  连接失败自动回退纯内存模式（不崩溃）
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, cast

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


class MilvusBackend:
    """Milvus 向量数据库持久化后端

    作为内存存储的旁路持久化层：检索仍走内存（逻辑不变），
    数据通过 Milvus 跨会话保存与恢复。

    - 连接失败 / 服务不可用 → ``enabled=False``，调用方回退纯内存模式。
    - 集合不存在时按首个向量维度自动创建（cosine 相似度）。
    - 条目标签结构：``id``(VARCHAR 主键) / ``text`` / ``vector`` / ``metadata``(JSON)。
    """

    def __init__(self, config: VectorConfig) -> None:
        self.enabled = False
        self._client: Any | None = None
        self._collection = config.milvus_collection
        # 集合就绪标志：创建成功后缓存，避免每次写入都做 has_collection 网络往返
        self._collection_ready = False
        # 后端线程安全锁（写入经 asyncio.to_thread 在线程池并发执行）
        self._lock = threading.RLock()
        try:
            from pymilvus import MilvusClient  # 延迟导入，pymilvus 不可用时优雅降级

            client = MilvusClient(uri=config.milvus_uri, timeout=5)
            # 列出已有集合：既验证连接可用，又避免重复查询——若目标集合已存在
            # 则直接置就绪标志，首次写入时跳过 _ensure_collection 的 has_collection 往返。
            # 说明：pymilvus 2.6.x 的 list_collections 类型存根误标为协程，但运行时返回
            # 同步 list（不可 await），此处以实际运行行为为准，用 cast 纠正存根类型。
            existing: list[str] = cast(
                "list[str]", client.list_collections()
            )
            self._client = client
            if self._collection in existing:
                self._collection_ready = True
            self.enabled = True
            logger.info(
                "Milvus 已连接: uri=%s, collection=%s (集合已存在=%s)",
                config.milvus_uri,
                self._collection,
                self._collection in existing,
            )
        except Exception as e:
            logger.warning("Milvus 不可用，回退内存存储（无持久化）: %s", e)

    def _ensure_index(self, dim: int) -> bool:
        """确保集合已建向量索引，返回是否可用。

        Milvus 的 ``load_collection`` 要求集合必须有索引，否则报
        ``code=700 index not found``。集合可能已存在但索引缺失（如历史
        数据或创建中断），此处检测并在缺失时补建 COSINE 索引。
        """
        if not self.enabled or self._client is None:
            return False
        try:
            from pymilvus import MilvusException

            if not self._client.has_collection(self._collection):
                return False
            # describe_index 在无索引时抛 MilvusException(code=700)
            try:
                self._client.describe_index(self._collection)
                return True
            except MilvusException as e:
                if getattr(e, "code", None) != 700:
                    raise
            # 无索引 → 补建（维度来自集合 schema，不依赖外部参数）
            self._client.create_index(
                self._collection,
                field_name="vector",
                index_type="AUTO_INDEX",
                metric_type="COSINE",
            )
            logger.info("Milvus 索引已补建: %s (dim=%d)", self._collection, dim)
            return True
        except Exception as e:
            logger.warning("Milvus 索引检查/补建失败: %s", e)
            return False

    def _ensure_collection(self, dim: int) -> bool:
        """确保集合存在且已建索引，返回是否可用。

        集合创建成功后缓存 ``_collection_ready`` 标志，后续写入跳过
        ``has_collection`` 检查（减少网络往返）；并发写由锁串行化。
        新建集合后立即补建索引，避免后续 ``load_collection`` 因无索引失败。
        """
        if not self.enabled or self._client is None:
            return False
        if self._collection_ready:
            # 即使就绪也需保证索引存在（历史集合可能无索引）
            self._ensure_index(dim)
            return True
        with self._lock:
            try:
                if not self._client.has_collection(self._collection):
                    from pymilvus import CollectionSchema, DataType, FieldSchema

                    schema = CollectionSchema(
                        [
                            FieldSchema(
                                name="id",
                                dtype=DataType.VARCHAR,
                                is_primary=True,
                                max_length=64,
                            ),
                            FieldSchema(
                                name="text", dtype=DataType.VARCHAR, max_length=65535
                            ),
                            FieldSchema(
                                name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim
                            ),
                            FieldSchema(name="metadata", dtype=DataType.JSON),
                        ]
                    )
                    self._client.create_collection(self._collection, schema=schema)
                    logger.info(
                        "Milvus 集合已创建: %s (dim=%d)", self._collection, dim
                    )
                self._ensure_index(dim)
                self._collection_ready = True
                return True
            except Exception as e:
                logger.warning("Milvus 集合创建失败: %s", e)
                return False

    def upsert_many(self, items: Sequence[VectorItem]) -> None:
        """批量写入 / 更新条目到 Milvus（同步阻塞，调用方用线程执行）。"""
        if not self.enabled or not items or self._client is None:
            return
        if not self._ensure_collection(len(items[0].vector)):
            return
        rows = [
            {
                "id": it.id,
                "text": it.text,
                "vector": it.vector,
                "metadata": {**it.metadata, "created_at": it.created_at},
            }
            for it in items
        ]
        try:
            with self._lock:
                self._client.upsert(self._collection, rows)
        except Exception as e:
            logger.warning("Milvus upsert 失败: %s", e)

    def delete(self, item_id: str) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            with self._lock:
                self._client.delete(self._collection, filter=f'id == "{item_id}"')
        except Exception as e:
            logger.warning("Milvus delete 失败: %s", e)

    def clear(self) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            with self._lock:
                self._client.drop_collection(self._collection)
            self._collection_ready = False
        except Exception as e:
            logger.warning("Milvus clear 失败: %s", e)

    def load_all(self) -> List[VectorItem]:
        """从 Milvus 读取全部条目（启动跨会话恢复用）。"""
        if not self.enabled or self._client is None:
            return []
        try:
            with self._lock:
                if not self._client.has_collection(self._collection):
                    return []
                # 集合存在但可能无索引，load 前补建（见 _ensure_index 说明）
                if not self._ensure_index(0):
                    return []
                self._client.load_collection(self._collection)
                results = self._client.query(
                    self._collection,
                    filter='id != ""',
                    output_fields=["id", "text", "vector", "metadata"],
                    limit=16384,
                )
            items: List[VectorItem] = []
            for r in results:
                meta = dict(r.get("metadata") or {})
                created_at = float(meta.pop("created_at", time.time()))
                items.append(
                    VectorItem(
                        id=str(r["id"]),
                        text=r["text"],
                        vector=list(r["vector"]),
                        metadata=meta,
                        created_at=created_at,
                    )
                )
            return items
        except Exception as e:
            logger.warning("Milvus 加载失败: %s", e)
            return []


class VectorStore:
    """内存向量存储与检索引擎（可选 Milvus 持久化）

    检索基于进程内存（余弦相似度，逻辑快且一致）；
    当配置 ``storage=milvus`` 时，写入同步持久化到 Milvus 向量数据库，
    构造时自动恢复历史条目，实现跨会话记忆；Milvus 不可用则回退纯内存模式。

    Args:
        embedding: 向量化提供者。
        config:    向量模块配置（相似度阈值、top_k、storage 等）。
    """

    def __init__(self, embedding: EmbeddingProvider, config: VectorConfig) -> None:
        self._embedding = embedding
        self._config = config
        self._items: Dict[str, VectorItem] = {}
        self._dimension: Optional[int] = embedding.dimension or None
        self._lock = threading.RLock()
        # Milvus 持久化后端（可选，storage=milvus 时启用；连接失败静默降级为纯内存）
        self._milvus: MilvusBackend | None = None
        if getattr(config, "storage", "memory") == "milvus":
            backend = MilvusBackend(config)
            if backend.enabled:
                self._milvus = backend
                self._restore_from_milvus()

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
        iid = self._add_with_vector(text, vector, metadata=metadata, item_id=item_id)
        await self._persist_async([self._items[iid]])
        return iid

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
            persisted = [self._items[iid] for iid in ids]
        await self._persist_async(persisted)
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

    # ── Milvus 持久化 ─────────────────────────────────────────

    async def _persist_async(self, items: Sequence[VectorItem]) -> None:
        """将内存条目异步持久化到 Milvus（线程池执行，不阻塞事件循环）。"""
        if self._milvus is None or not self._milvus.enabled:
            return
        try:
            await asyncio.to_thread(self._milvus.upsert_many, list(items))
        except Exception as e:
            logger.warning("Milvus 持久化失败: %s", e)

    def _restore_from_milvus(self) -> None:
        """启动时从 Milvus 恢复全部条目（跨会话恢复）。"""
        if self._milvus is None:
            return
        items = self._milvus.load_all()
        if not items:
            return
        with self._lock:
            for it in items:
                if it.id in self._items:
                    continue
                self._items[it.id] = it
                if self._dimension is None:
                    self._dimension = len(it.vector)
        logger.info("从 Milvus 恢复向量记忆 %d 条", len(items))

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
        """删除指定 ID 的条目（同步同步到 Milvus）。

        Returns:
            删除成功返回 True，条目不存在返回 False。
        """
        with self._lock:
            if item_id not in self._items:
                return False
            del self._items[item_id]
        if self._milvus is not None and self._milvus.enabled:
            self._milvus.delete(item_id)
        return True

    def find_ids(
        self,
        text: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> List[str]:
        """按文本与元数据精确匹配查找条目 ID（内存遍历，非索引查询）。

        常用于定位待删除条目（如记忆遗忘清理）。text 为精确匹配；
        metadata 的每个键值都需与条目元数据一致才算命中。

        Args:
            text:     精确匹配的条目文本；None 表示不限制。
            metadata: 需全部命中的元数据键值对；None 表示不限制。

        Returns:
            命中的条目 ID 列表（顺序无保证）。
        """
        with self._lock:
            return [
                it.id
                for it in self._items.values()
                if (text is None or it.text == text)
                and (
                    metadata is None
                    or all(it.metadata.get(k) == v for k, v in metadata.items())
                )
            ]

    def delete_many(self, item_ids: Sequence[str]) -> int:
        """批量删除条目（同步同步到 Milvus）。

        Args:
            item_ids: 待删除条目 ID 列表。

        Returns:
            实际删除的条目数（不存在的 ID 忽略）。
        """
        deleted = 0
        with self._lock:
            for iid in item_ids:
                if iid in self._items:
                    del self._items[iid]
                    deleted += 1
        if deleted and self._milvus is not None and self._milvus.enabled:
            for iid in item_ids:
                self._milvus.delete(iid)
        return deleted

    def clear(self) -> None:
        """清空全部条目并重置维度（同步清空 Milvus 集合）。"""
        with self._lock:
            self._items.clear()
            self._dimension = None
        if self._milvus is not None and self._milvus.enabled:
            self._milvus.clear()

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
