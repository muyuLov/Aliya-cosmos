"""ContinuityLayer（L2）：近期场景摘要 + 连续性快照层。

存储近期场景摘要与连续性快照，供主叙事低频状态参考。写入按时间倒序，
``query()`` 返回最近写入的摘要（近期优先）。
"""
from __future__ import annotations

from typing import Any

from core.memory.layers import MemoryEntry, MemoryLayer


class ContinuityLayer(MemoryLayer):
    """场景摘要连续性层。"""

    name = "continuity"

    def __init__(self, capacity: int = 50) -> None:
        self._entries: list[MemoryEntry] = []
        self._capacity = capacity

    async def write(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._capacity:
            self._entries = self._entries[-self._capacity:]

    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]:
        # 近期优先：倒序返回最新写入的摘要
        return list(reversed(self._entries))[:limit]

    async def forget(self, entry_id: str) -> None:
        self._entries = [e for e in self._entries if e.id != entry_id]

    async def decay(self, factor: float = 0.95) -> None:
        for e in self._entries:
            if e.confidence > 0:
                self._entries[self._entries.index(e)] = MemoryEntry(
                    id=e.id,
                    content=e.content,
                    source=e.source,
                    confidence=e.confidence * factor,
                    importance=e.importance,
                    metadata=e.metadata,
                )
