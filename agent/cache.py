"""Agent 性能优化模块：缓存、连接池等"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    created_at: float
    ttl: float


class LRUCache(Generic[T]):
    """LRU 缓存实现，基于 OrderedDict（O(1) 操作）"""

    def __init__(self, max_size: int = 100, default_ttl: float = 3600.0):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._cache.get(key)
        if entry is None:
            return None

        if time.time() - entry.created_at > entry.ttl:
            del self._cache[key]
            return None

        self._cache.move_to_end(key)
        return entry.value

    def set(self, key: str, value: T, ttl: float | None = None) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        elif self.max_size > 0 and len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = CacheEntry(
            value=value,
            created_at=time.time(),
            ttl=ttl or self.default_ttl,
        )

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        entry = self._cache.get(key)
        if entry is None:
            return False
        if time.time() - entry.created_at > entry.ttl:
            del self._cache[key]
            return False
        return True


class MemoryRetrievalCache:
    """记忆检索专用缓存"""

    def __init__(self, max_size: int = 50, ttl: float = 300.0):
        self._cache = LRUCache[list](max_size=max_size, default_ttl=ttl)
        self._logger = get_logger(__name__)

    def _make_key(self, query: str, limit: int) -> str:
        return hashlib.sha256(f"{query}:{limit}".encode()).hexdigest()

    def get(self, query: str, limit: int) -> list | None:
        key = self._make_key(query, limit)
        result = self._cache.get(key)
        if result is not None:
            self._logger.debug("缓存命中：%s...", query[:50])
        return result

    def set(self, query: str, limit: int, memories: list) -> None:
        key = self._make_key(query, limit)
        self._cache.set(key, memories)
        self._logger.debug("缓存已设置：%s...", query[:50])

    def clear(self) -> None:
        self._cache.clear()
        self._logger.info("记忆缓存已清空")


def format_memory_list(memories: list, empty_text: str, prefix: str = "") -> str:
    """
    将记忆列表格式化为可读文本。

    每条记忆支持两种格式：
    - 三元组 (subject, predicate, object)：展示为 "N. s p o"
    - 其他：直接 str() 展示

    Args:
        memories: 记忆列表，每项为三元组或任意对象。
        empty_text: 列表为空时返回的文本。
        prefix: 非空时作为第一行标题插入结果头部。
    """
    if not memories:
        return empty_text
    parts = [prefix] if prefix else []
    for i, item in enumerate(memories, 1):
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            parts.append(f"{i}. {item[0]} {item[1]} {item[2]}")
        else:
            parts.append(f"{i}. {item}")
    return "\n".join(parts)


@dataclass
class PerformanceMetrics:
    """性能指标统计"""

    cache_hits: int = 0
    cache_misses: int = 0
    avg_retrieval_time_ms: float = 0.0
    total_requests: int = 0

    _retrieval_count: int = field(default=0, init=False, repr=False)
    _retrieval_sum: float = field(default=0.0, init=False, repr=False)

    def record_retrieval(self, duration_ms: float) -> None:
        self._retrieval_count += 1
        self._retrieval_sum += duration_ms
        self.avg_retrieval_time_ms = self._retrieval_sum / self._retrieval_count

    def record_cache_hit(self) -> None:
        self.cache_hits += 1
        self.total_requests += 1

    def record_cache_miss(self) -> None:
        self.cache_misses += 1
        self.total_requests += 1

    @property
    def cache_hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": self.cache_hit_rate,
            "avg_retrieval_time_ms": self.avg_retrieval_time_ms,
            "total_requests": self.total_requests,
        }
