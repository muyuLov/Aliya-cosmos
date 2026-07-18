"""测试 LLM 缓存模块：MemoryBackend LRU 淘汰策略、ContextCache TTL 过期与边界情况"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from core.llm.cache import ContextCache
from core.llm.cache_backend import MemoryBackend
from core.llm.exceptions import ContextCacheError
from core.llm.models import ConversationContext


class TestMemoryBackend:
    def test_set_and_get(self):
        backend = MemoryBackend()
        ctx = ConversationContext(conversation_id="c1")
        backend.set("k1", ctx, 100.0)
        entry = backend.get("k1")
        assert entry is not None
        assert entry[0] is ctx
        assert entry[1] == 100.0

    def test_get_missing_returns_none(self):
        backend = MemoryBackend()
        assert backend.get("nonexistent") is None

    def test_delete_existing(self):
        backend = MemoryBackend()
        backend.set("k1", ConversationContext(conversation_id="c1"), 1.0)
        backend.delete("k1")
        assert backend.get("k1") is None

    def test_delete_missing_no_error(self):
        backend = MemoryBackend()
        backend.delete("nonexistent")  # 不应抛异常

    def test_clear(self):
        backend = MemoryBackend()
        backend.set("k1", ConversationContext(conversation_id="c1"), 1.0)
        backend.set("k2", ConversationContext(conversation_id="c2"), 2.0)
        backend.clear()
        assert backend.size == 0
        assert backend.get("k1") is None

    def test_get_all_keys(self):
        backend = MemoryBackend()
        backend.set("a", ConversationContext(conversation_id="c1"), 1.0)
        backend.set("b", ConversationContext(conversation_id="c2"), 2.0)
        keys = backend.get_all_keys()
        assert "a" in keys
        assert "b" in keys
        assert len(keys) == 2

    def test_exists(self):
        backend = MemoryBackend()
        backend.set("k", ConversationContext(conversation_id="c"), 1.0)
        assert backend.exists("k")
        assert not backend.exists("missing")

    def test_lru_get_moves_to_end(self):
        backend = MemoryBackend()
        ctx_a = ConversationContext(conversation_id="a")
        ctx_b = ConversationContext(conversation_id="b")
        backend.set("a", ctx_a, 1.0)
        backend.set("b", ctx_b, 2.0)
        # get('a') 使 a 变最新
        backend.get("a")
        keys = backend.get_all_keys()
        # b 应是最久未使用的，a 是最近使用的
        assert keys == ["b", "a"]

    def test_lru_eviction(self):
        backend = MemoryBackend(max_size=2)
        backend.set("a", ConversationContext(conversation_id="a"), 1.0)
        backend.set("b", ConversationContext(conversation_id="b"), 2.0)
        backend.set("c", ConversationContext(conversation_id="c"), 3.0)  # 淘汰 a
        assert backend.get("a") is None  # 被淘汰
        assert backend.get("b") is not None
        assert backend.get("c") is not None
        assert backend.size == 2

    def test_lru_update_refreshes_position(self):
        backend = MemoryBackend(max_size=2)
        backend.set("a", ConversationContext(conversation_id="a"), 1.0)
        backend.set("b", ConversationContext(conversation_id="b"), 2.0)
        # 更新 a 使其变最新
        backend.set("a", ConversationContext(conversation_id="a-updated"), 3.0)
        # 现在 b 是最久未使用，新写入 c 应淘汰 b
        backend.set("c", ConversationContext(conversation_id="c"), 4.0)
        assert backend.get("a") is not None
        assert backend.get("b") is None  # 被淘汰
        assert backend.get("c") is not None

    def test_unlimited_size(self):
        backend = MemoryBackend(max_size=0)
        for i in range(1000):
            backend.set(f"k{i}", ConversationContext(conversation_id=str(i)), float(i))
        assert backend.size == 1000


class TestContextCache:
    def test_set_and_get(self):
        cache = ContextCache(ttl=3600)
        ctx = ConversationContext(conversation_id="c1")
        cache.set("c1", ctx)
        assert cache.get("c1") is ctx

    def test_get_missing_returns_none(self):
        cache = ContextCache()
        assert cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self):
        """TTL 过期后，get 应返回 None 并自动删除过期条目"""
        cache = ContextCache(ttl=0.01)  # 10ms TTL
        ctx = ConversationContext(conversation_id="c1")
        cache.set("c1", ctx)
        time.sleep(0.02)
        assert cache.get("c1") is None

    def test_delete(self):
        cache = ContextCache()
        cache.set("c1", ConversationContext(conversation_id="c1"))
        cache.delete("c1")
        assert cache.get("c1") is None

    def test_delete_missing_no_error(self):
        cache = ContextCache()
        cache.delete("nonexistent")  # 不应抛异常

    def test_clear(self):
        cache = ContextCache()
        cache.set("a", ConversationContext(conversation_id="a"))
        cache.set("b", ConversationContext(conversation_id="b"))
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_exists(self):
        cache = ContextCache(ttl=3600)
        assert not cache.exists("missing")
        cache.set("k", ConversationContext(conversation_id="k"))
        assert cache.exists("k")

    def test_exists_expired_returns_false(self):
        cache = ContextCache(ttl=0.01)
        cache.set("k", ConversationContext(conversation_id="k"))
        time.sleep(0.02)
        assert not cache.exists("k")

    def test_ttl_zero_never_expires(self):
        cache = ContextCache(ttl=0)
        ctx = ConversationContext(conversation_id="c1")
        cache.set("c1", ctx)
        time.sleep(0.02)
        assert cache.get("c1") is ctx  # 永不过期

    def test_evict_expired(self):
        cache = ContextCache(ttl=0.01)
        cache.set("a", ConversationContext(conversation_id="a"))
        cache.set("b", ConversationContext(conversation_id="b"))
        time.sleep(0.02)
        count = cache.evict_expired()
        assert count == 2
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_evict_expired_ttl_zero_returns_zero(self):
        cache = ContextCache(ttl=0)
        count = cache.evict_expired()
        assert count == 0

    def test_evict_expired_no_expired(self):
        cache = ContextCache(ttl=3600)
        cache.set("a", ConversationContext(conversation_id="a"))
        count = cache.evict_expired()
        assert count == 0
        assert cache.get("a") is not None

    def test_backend_injection(self):
        backend = MemoryBackend(max_size=10)
        cache = ContextCache(ttl=3600, backend=backend)
        ctx = ConversationContext(conversation_id="c")
        cache.set("c", ctx)
        assert cache.get("c") is ctx
        assert backend.size == 1

    def test_get_raises_context_cache_error(self):
        """后端抛 KeyError 时应包装为 ContextCacheError"""
        cache = ContextCache(ttl=3600)
        with patch.object(cache._backend, "get", side_effect=KeyError("bad key")):
            with pytest.raises(ContextCacheError, match="读取会话缓存失败"):
                cache.get("bad")
