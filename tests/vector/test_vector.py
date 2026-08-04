"""测试 core.vector 向量模块：存储检索、配置、工厂、资源管理"""

from __future__ import annotations

import math
import zlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.vector.config import (
    EmbeddingConfig,
    VectorConfig,
    _check_type,
    _load_vector_config,
)
from core.vector.embedding import EmbeddingFactory, EmbeddingProvider
from core.vector.exceptions import (
    DimensionMismatchError,
    StoreError,
    VectorConfigError,
    VectorNotEnabledError,
)
from core.vector.store import (
    VectorStore,
    get_vector_store,
    reset_vector_store,
    shutdown_vector_store,
)


class FakeEmbeddingProvider(EmbeddingProvider):
    """测试用确定性向量化提供者：字符 crc32 哈希特征向量（L2 归一化）。"""

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return "fake"

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * self._dimension
            for ch in text:
                vec[zlib.crc32(ch.encode("utf-8")) % self._dimension] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


# ── 存储与检索 ──────────────────────────────────────────────


def _make_store(dimension: int = 64, **cfg_kwargs) -> VectorStore:
    cfg = VectorConfig(**cfg_kwargs)
    return VectorStore(embedding=FakeEmbeddingProvider(dimension), config=cfg)


class TestVectorStore:
    async def test_add_and_get(self):
        store = _make_store()
        iid = await store.add("我喜欢喝咖啡", metadata={"tag": "food"})
        assert iid
        item = store.get(iid)
        assert item is not None
        assert item.text == "我喜欢喝咖啡"
        assert item.metadata == {"tag": "food"}
        assert store.count == 1

    async def test_add_custom_id(self):
        store = _make_store()
        iid = await store.add("测试", item_id="custom-1")
        assert iid == "custom-1"

    async def test_add_duplicate_id_raises(self):
        store = _make_store()
        await store.add("第一条", item_id="dup")
        with pytest.raises(Exception, match="已存在相同 ID"):
            await store.add("第二条", item_id="dup")

    async def test_search_finds_similar(self):
        store = _make_store(similarity_threshold=0.0)
        await store.add_many(
            [
                {"text": "我喜欢喝咖啡", "metadata": {"topic": "food"}},
                {"text": "今天天气晴朗适合出游", "metadata": {"topic": "weather"}},
                {"text": "我在学习 Python 编程", "metadata": {"topic": "tech"}},
            ]
        )
        results = await store.search_async("我爱好喝咖啡", top_k=3)
        assert results
        assert results[0].text == "我喜欢喝咖啡"

    async def test_search_threshold_filters(self):
        store = _make_store(similarity_threshold=0.9)
        await store.add("我喜欢喝咖啡")
        await store.add("今天股票大涨")
        results = await store.search_async("我喜欢喝咖啡", top_k=5)
        assert len(results) == 1

    async def test_search_top_k_limit(self):
        store = _make_store(similarity_threshold=0.0)
        await store.add_many(
            [{"text": f"共同话题{chr(20000 + i)}的内容"} for i in range(10)]
        )
        results = await store.search_async("共同话题", top_k=3)
        assert len(results) == 3

    async def test_search_empty_query(self):
        store = _make_store()
        await store.add("内容")
        assert await store.search_async("") == []

    async def test_add_many_returns_ids(self):
        store = _make_store()
        ids = await store.add_many([{"text": "a"}, {"text": "b"}])
        assert len(ids) == 2
        assert store.count == 2

    async def test_delete(self):
        store = _make_store()
        iid = await store.add("内容")
        assert store.delete(iid) is True
        assert store.count == 0

    def test_delete_missing_returns_false(self):
        store = _make_store()
        assert store.delete("not-exist") is False

    async def test_clear(self):
        store = _make_store()
        await store.add_many([{"text": "a"}, {"text": "b"}])
        store.clear()
        assert store.count == 0
        assert store.dimension == 0

    def test_dimension_known_from_provider(self):
        """store 预置 provider 已知维度：配置维度的空库即可知维度，无需等首次入库"""
        store = _make_store(dimension=64)
        assert store.count == 0
        assert store.dimension == 64

    async def test_dimension_mismatch_raises(self):
        store = _make_store(dimension=64)
        await store.add("第一条")

        class WrongDimProvider(FakeEmbeddingProvider):
            def __init__(self):
                super().__init__(dimension=32)

        store._embedding = WrongDimProvider()
        with pytest.raises(DimensionMismatchError):
            await store.add("维度不同的条目")

    async def test_in_memory_only(self):
        """向量仅存内存：新实例不共享旧数据。"""
        store1 = _make_store()
        await store1.add("我喜欢喝咖啡")
        store2 = _make_store()
        assert store2.count == 0

    async def test_search_query_dimension_mismatch(self):
        """查询向量与库中条目维度不一致时尽早抛错。"""
        store = _make_store(dimension=64)
        await store.add("内容")

        class WrongDimProvider(FakeEmbeddingProvider):
            def __init__(self):
                super().__init__(dimension=32)

        store._embedding = WrongDimProvider()
        with pytest.raises(DimensionMismatchError):
            await store.search_async("查询")

    async def test_aclose_forwarded(self):
        """VectorStore.aclose 转发到底层 embedding 提供者。"""
        store = _make_store()
        closed = []

        class ClosingProvider(FakeEmbeddingProvider):
            async def aclose(self):
                closed.append(1)

        store._embedding = ClosingProvider()
        await store.aclose()
        assert closed == [1]

    async def test_aclose_no_provider_support(self):
        """底层 provider 无 aclose 时安全跳过（getattr 防御分支）。"""
        store = _make_store()
        await store.aclose()  # FakeEmbeddingProvider 无 aclose，不应抛错

    async def test_add_blank_text_raises(self):
        """空白文本被拒绝，不向量化不入库。"""
        store = _make_store()
        for blank in ("", "   ", "\t\n"):
            with pytest.raises(StoreError, match="空白"):
                await store.add(blank)
        assert store.count == 0

    async def test_add_many_blank_text_raises(self):
        """批量添加含空白文本时整体拒绝，且不写入任何条目。"""
        store = _make_store()
        with pytest.raises(StoreError, match="空白"):
            await store.add_many([{"text": "正常文本"}, {"text": "  "}])
        assert store.count == 0

    async def test_add_many_atomic_on_id_conflict(self):
        """批量添加中 ID 冲突时整体失败，不产生部分写入（原子性）。"""
        store = _make_store()
        await store.add("已有条目", item_id="existing")
        with pytest.raises(Exception, match="已存在相同 ID"):
            await store.add_many(
                [{"text": "新条目A"}, {"id": "existing", "text": "冲突条目"}]
            )
        assert store.count == 1  # 只有预先添加的那条

    def test_cosine_dimension_mismatch_returns_zero(self):
        """_cosine 对维度不一致返回 0.0，不做静默截断。"""
        assert VectorStore._cosine([1.0, 0.0], [0.0, 1.0, 0.0]) == 0.0
        assert VectorStore._cosine([], []) == 0.0

    def test_batch_cosine_matches_single(self):
        """_batch_cosine（numpy 批量）与 _cosine 单条结果一致。"""
        from core.vector.store import _batch_cosine

        vectors = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]
        query = [1.0, 0.0]
        batch = _batch_cosine(vectors, query)
        assert len(batch) == 3
        for vec, score in zip(vectors, batch):
            assert score == pytest.approx(VectorStore._cosine(vec, query))

    def test_batch_cosine_empty_and_mismatch(self):
        """_batch_cosine 空输入返回空列表，维度不一致返回全 0。"""
        from core.vector.store import _batch_cosine

        assert _batch_cosine([], [1.0]) == []
        assert _batch_cosine([[1.0, 0.0]], [0.0, 1.0, 0.0]) == [0.0]


# ── 配置 ────────────────────────────────────────────────────


class TestVectorConfig:
    def test_defaults(self):
        cfg = VectorConfig()
        assert cfg.enabled is True
        assert cfg.similarity_threshold == 0.5
        assert cfg.top_k == 5
        assert isinstance(cfg.embedding, EmbeddingConfig)
        assert cfg.embedding.dimension == 0  # 默认未知，由 API 返回自动推断

    def test_check_type_valid(self):
        _check_type(128, "dimension", int, min_val=16)

    def test_check_type_invalid(self):
        with pytest.raises(VectorConfigError, match="期望类型"):
            _check_type("abc", "dimension", int)

    def test_check_type_below_min(self):
        with pytest.raises(VectorConfigError, match="小于最小值"):
            _check_type(0, "dimension", int, min_val=16)

    @patch("core.vector.config.get_config_instance")
    def test_load_full_config(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.vector.enabled": True,
            "cosmos.service.vector.similarity_threshold": 0.7,
            "cosmos.service.vector.top_k": 8,
            "cosmos.service.vector.embedding": {
                "model": "text-embedding-3-small",
                "url": "https://api.example.com",
                "api_key": "secret",
                "batch_size": 32,
                "dimension": 1536,
            },
        }.get(key, default)

        cfg = _load_vector_config("test.yml")
        assert cfg.similarity_threshold == 0.7
        assert cfg.top_k == 8
        assert cfg.embedding.model == "text-embedding-3-small"
        assert cfg.embedding.batch_size == 32
        assert cfg.embedding.dimension == 1536

    @patch("core.vector.config.get_config_instance")
    def test_load_invalid_dimension(self, mock_get_cfg):
        """dimension 非整数时抛配置错误"""
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.vector.embedding": {"dimension": "big"},
        }.get(key, default)
        with pytest.raises(VectorConfigError, match="dimension"):
            _load_vector_config("test.yml")


# ── EmbeddingProvider 维度初始化 ─────────────────────────────────


class TestEmbeddingProviderDimension:
    def test_config_dimension_initializes_provider(self):
        """配置的 dimension 作为提供者初始已知维度，无需等待首次向量化"""
        from core.vector.config import EmbeddingConfig
        from core.vector.embedding import OpenAIEmbeddingProvider

        config = EmbeddingConfig(
            model="text-embedding-3-small",
            url="https://api.example.com",
            api_key="key",
            dimension=1536,
        )
        provider = OpenAIEmbeddingProvider(config)
        assert provider.dimension == 1536

    def test_zero_dimension_stays_unknown(self):
        """dimension=0（未知）时提供者维度为 0，由 API 返回推断"""
        from core.vector.config import EmbeddingConfig
        from core.vector.embedding import OpenAIEmbeddingProvider

        config = EmbeddingConfig(
            model="text-embedding-3-small",
            url="https://api.example.com",
            api_key="key",
            dimension=0,
        )
        provider = OpenAIEmbeddingProvider(config)
        assert provider.dimension == 0

    async def test_missing_api_key_uses_placeholder(self):
        """api_key 可留空：本地服务用占位符满足 SDK 约束。"""
        from core.vector.embedding import OpenAIEmbeddingProvider

        config = EmbeddingConfig(model="m", url="http://x", api_key="")
        provider = OpenAIEmbeddingProvider(config)
        assert provider._client.api_key == "embedding"

    async def test_embed_dimension_mismatch_raises(self):
        """配置期望维度后，API 返回维度不一致时尽早抛错。"""
        from core.vector.embedding import OpenAIEmbeddingProvider

        config = EmbeddingConfig(
            model="m",
            url="http://x",
            api_key="k",
            dimension=1536,
        )
        provider = OpenAIEmbeddingProvider(config)

        async def fake_create(**kwargs):
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.0] * 1024)])

        provider._client = SimpleNamespace(embeddings=SimpleNamespace(create=fake_create))
        with pytest.raises(DimensionMismatchError):
            await provider.embed(["文本"])

    @patch("core.vector.config.get_config_instance")
    def test_load_missing_sections_use_defaults(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {}.get(key, default)
        cfg = _load_vector_config("test.yml")
        assert cfg.embedding.model == ""
        assert cfg.similarity_threshold == 0.5


# ── 工厂与单例 ──────────────────────────────────────────────


class TestFactoryAndSingleton:
    def test_factory_missing_model_raises(self):
        cfg = VectorConfig(
            embedding=EmbeddingConfig(url="http://x", api_key="k")
        )
        with pytest.raises(VectorConfigError, match="model"):
            EmbeddingFactory.create(cfg)

    def test_get_vector_store_singleton(self):
        with patch("core.vector.store.get_vector_config") as mock_cfg:
            mock_cfg.return_value = VectorConfig(
                embedding=EmbeddingConfig(model="m", url="http://x", api_key="k"),
            )
            with patch.object(
                EmbeddingFactory, "create", return_value=FakeEmbeddingProvider(64)
            ):
                reset_vector_store()
                store1 = get_vector_store()
                store2 = get_vector_store()
                assert store1 is store2
                reset_vector_store()

    def test_disabled_raises(self):
        with patch("core.vector.store.get_vector_config") as mock_cfg:
            mock_cfg.return_value = VectorConfig(enabled=False)
            reset_vector_store()
            with pytest.raises(VectorNotEnabledError):
                get_vector_store()
            reset_vector_store()

    async def test_shutdown_vector_store(self):
        """shutdown_vector_store 关闭底层资源并重置单例，之后可重新创建。"""
        closed = []

        class ClosingProvider(FakeEmbeddingProvider):
            async def aclose(self):
                closed.append(1)

        with patch("core.vector.store.get_vector_config") as mock_cfg:
            mock_cfg.return_value = VectorConfig(
                embedding=EmbeddingConfig(model="m", url="http://x", api_key="k"),
            )
            with patch.object(
                EmbeddingFactory, "create", return_value=ClosingProvider()
            ):
                reset_vector_store()
                assert get_vector_store() is not None
                await shutdown_vector_store()
                assert closed == [1]
                # 关闭后单例已重置，可重新创建
                assert get_vector_store() is not None
