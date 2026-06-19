"""缓存后端：仅支持内存存储"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm.models import ConversationContext


class CacheBackend(ABC):
    """缓存后端抽象基类"""

    @abstractmethod
    def get(self, key: str) -> tuple[ConversationContext, float] | None:
        """
        获取缓存条目。

        Args:
            key: 缓存键。

        Returns:
            (上下文对象, 存储时间戳) 元组，不存在时返回 None。
        """
        pass

    @abstractmethod
    def set(self, key: str, context: ConversationContext, stored_at: float) -> None:
        """
        存储缓存条目。

        Args:
            key: 缓存键。
            context: 上下文对象。
            stored_at: 存储时间戳。
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除缓存条目。"""
        pass

    @abstractmethod
    def clear(self) -> None:
        """清空所有缓存。"""
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """检查键是否存在。"""
        pass

    @abstractmethod
    def get_all_keys(self) -> list[str]:
        """返回所有键（用于过期清理等遍历场景）。"""
        pass


class MemoryBackend(CacheBackend):
    """内存缓存后端，使用 OrderedDict 实现 LRU 淘汰策略。

    Args:
        max_size: 最大缓存条目数，0 表示不限制（默认不限制）。
    """

    def __init__(self, max_size: int = 0) -> None:
        self._max_size = max_size
        self._store: OrderedDict[str, tuple[ConversationContext, float]] = OrderedDict()

    def get(self, key: str) -> tuple[ConversationContext, float] | None:
        entry = self._store.get(key)
        if entry is not None:
            # LRU：命中时移到末尾（最近使用）
            self._store.move_to_end(key)
            return entry
        return None

    def set(self, key: str, context: ConversationContext, stored_at: float) -> None:
        if key in self._store:
            # 已存在：更新值并移到末尾
            self._store.move_to_end(key)
        else:
            # 新条目：检查是否需要淘汰
            if self._max_size > 0 and len(self._store) >= self._max_size:
                self._store.popitem(last=False)  # 淘汰最久未使用的条目
        self._store[key] = (context, stored_at)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def exists(self, key: str) -> bool:
        return key in self._store

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        return len(self._store)

    def get_all_keys(self) -> list[str]:
        """返回所有键（用于过期清理）"""
        return list(self._store.keys())


