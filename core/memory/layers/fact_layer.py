"""FactLayer（L3）：长期事实/承诺/事件层（全新实现）。

存储长期事实，检索时按加权排序：
重要性 0.5 / 置信度 0.35 / 时效 0.15。
"""
from __future__ import annotations

from core.memory.layers import MemoryEntry, MemoryLayer

# 加权排序权重
_W_IMPORTANCE = 0.5
_W_CONFIDENCE = 0.35
_W_RECENCY = 0.15


class FactLayer(MemoryLayer):
    """长期事实层（全新实现）。"""

    name = "fact"

    def __init__(self, capacity: int = 500) -> None:
        self._entries: list[MemoryEntry] = []
        self._capacity = capacity
        self._clock = 0  # 单调递增的时效时钟（越大越新）

    async def write(self, entry: MemoryEntry) -> None:
        self._clock += 1
        meta = dict(entry.metadata)
        meta["_recency"] = self._clock
        stored = MemoryEntry(
            id=entry.id,
            content=entry.content,
            source=entry.source,
            confidence=entry.confidence,
            importance=entry.importance,
            metadata=meta,
        )
        self._entries.append(stored)
        if len(self._entries) > self._capacity:
            # 淘汰加权分最低的条目
            self._entries.sort(key=self._score, reverse=True)
            self._entries = self._entries[: self._capacity]

    def _score(self, entry: MemoryEntry) -> float:
        """加权排序分：重要性×0.5 + 置信度×0.35 + 时效×0.15。"""
        importance = max(0.0, min(1.0, entry.importance))
        confidence = max(0.0, min(1.0, entry.confidence))
        recency = entry.metadata.get("_recency", 0)
        # 时效：当前时钟下该条目的相对新鲜度（0~1）
        recency_norm = recency / self._clock if self._clock else 0.0
        return _W_IMPORTANCE * importance + _W_CONFIDENCE * confidence + _W_RECENCY * recency_norm

    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]:
        ranked = sorted(self._entries, key=self._score, reverse=True)
        return ranked[:limit]

    async def forget(self, entry_id: str) -> None:
        self._entries = [e for e in self._entries if e.id != entry_id]

    async def decay(self, factor: float = 0.95) -> None:
        updated: list[MemoryEntry] = []
        for e in self._entries:
            updated.append(
                MemoryEntry(
                    id=e.id,
                    content=e.content,
                    source=e.source,
                    confidence=e.confidence * factor,
                    importance=e.importance,
                    metadata=e.metadata,
                )
            )
        self._entries = updated
