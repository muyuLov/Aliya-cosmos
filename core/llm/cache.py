"""对话上下文缓存：仅支持内存后端"""

from __future__ import annotations

import time

from core.llm.cache_backend import CacheBackend, MemoryBackend
from core.llm.exceptions import ContextCacheError
from core.llm.models import ConversationContext


class ContextCache:
    """
    对话上下文缓存，以 conversation_id 为键存储 ConversationContext。

    支持 TTL 过期：每次 get() 时惰性检查，过期条目在访问时删除。
    支持 LRU 淘汰（MemoryBackend）：超出 max_size 时自动淘汰最久未使用的条目。

    仅支持内存后端：
    - MemoryBackend: 内存存储（OrderedDict + LRU），适用于单进程场景

    Args:
        ttl: 缓存条目存活秒数，默认 86400（24 小时）。设为 0 则永不过期。
        max_size: 最大缓存条目数，默认 500。设为 0 表示不限制。仅对 MemoryBackend 生效。
        backend: 缓存后端实例，默认使用 MemoryBackend（启用 LRU）。
    """

    def __init__(
        self,
        ttl: int = 86400,
        max_size: int = 500,
        backend: CacheBackend | None = None,
    ) -> None:
        if backend is None:
            backend = MemoryBackend(max_size=max_size)
        self._backend = backend
        self._ttl = ttl

    def get(self, conversation_id: str) -> ConversationContext | None:
        """
        获取指定会话的上下文，不存在或已过期时返回 None。

        Args:
            conversation_id: 会话唯一标识符。

        Returns:
            会话上下文对象，不存在或已过期时返回 None。

        Raises:
            ContextCacheError: 缓存读取发生意外错误时抛出。
        """
        try:
            entry = self._backend.get(conversation_id)
            if entry is None:
                return None
            context, stored_at = entry
            if self._is_expired(stored_at):
                self._backend.delete(conversation_id)
                return None
            return context
        except (KeyError, ValueError, TypeError) as exc:
            raise ContextCacheError(f"读取会话缓存失败: {conversation_id}", cause=exc) from exc

    def set(self, conversation_id: str, context: ConversationContext) -> None:
        """
        存储或覆盖会话上下文，同时刷新存入时间戳。
        若后端为 MemoryBackend 且设置了 max_size，超出限制时自动淘汰最久未使用的条目。

        Args:
            conversation_id: 会话唯一标识符。
            context: 待存储的会话上下文。

        Raises:
            ContextCacheError: 缓存写入发生意外错误时抛出。
        """
        try:
            self._backend.set(conversation_id, context, time.time())
        except (KeyError, ValueError, TypeError) as exc:
            raise ContextCacheError(f"写入会话缓存失败: {conversation_id}", cause=exc) from exc

    def delete(self, conversation_id: str) -> None:
        """删除指定会话，不存在时静默忽略。"""
        self._backend.delete(conversation_id)

    def clear(self) -> None:
        """清空所有缓存条目。"""
        self._backend.clear()

    def exists(self, conversation_id: str) -> bool:
        """检查会话是否存在且未过期（无副作用，不触发 LRU 或删除）。"""
        entry = self._backend.get(conversation_id)
        if entry is None:
            return False
        _, stored_at = entry
        return not self._is_expired(stored_at)

    def evict_expired(self) -> int:
        """
        主动清理所有已过期的条目。

        注意：get_all_keys() 返回的是调用时刻的快照，后续的并发 set 可能使
        新写入的条目被同一次清理扫到。由于 set() 写入时会设置当前时间戳，
        误删概率极低（仅在 TTL 边界 + 并发写入窗口内可能发生），因清理是
        辅助性的（惰性清理在每次 get() 时也会执行），误删后下次 set() 自动恢复。

        Returns:
            本次清理的条目数量。
        """
        if self._ttl <= 0:
            return 0

        now = time.time()
        count = 0
        for key in self._backend.get_all_keys():
            entry = self._backend.get(key)
            if entry is not None:
                _, stored_at = entry
                if (now - stored_at) > self._ttl:
                    self._backend.delete(key)
                    count += 1

        return count

    def _is_expired(self, stored_at: float) -> bool:
        return self._ttl > 0 and (time.time() - stored_at) > self._ttl
