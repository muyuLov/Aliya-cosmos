"""分层记忆统一门面

提供 UnifiedMemoryFacade，持有四层记忆（Canon / Overlay / Continuity / Fact），
对外暴露统一的 write / query / forget / decay 接口。

get_memory_manager() 返回该门面的全局单例。
"""

from __future__ import annotations

import threading
from typing import Optional

from core.memory.layers import MemoryEntry
from core.memory.layers.canon import CanonLayer
from core.memory.layers.overlay import OverlayLayer
from core.memory.layers.continuity import ContinuityLayer
from core.memory.layers.fact_layer import FactLayer


class UnifiedMemoryFacade:
    """四层分层记忆统一门面。

    持有 canon / overlay / continuity / fact 四层，对外提供：
    - write(entry)          → 写入 fact 层
    - write_continuity(entry) → 写入 continuity 层
    - query(text, limit)    → 跨层检索（fact + continuity）
    - forget(entry_id)      → 跨层删除
    - decay(factor)         → 跨层衰减
    """

    def __init__(self) -> None:
        self.canon: CanonLayer = CanonLayer()
        self.overlay: OverlayLayer = OverlayLayer()
        self.continuity: ContinuityLayer = ContinuityLayer()
        self.fact: FactLayer = FactLayer()

    # ── 写入 ──────────────────────────────────────────────

    async def write(self, entry: MemoryEntry) -> None:
        """写入 fact 层（长期事实）。"""
        await self.fact.write(entry)

    async def write_continuity(self, entry: MemoryEntry) -> None:
        """写入 continuity 层（场景摘要）。"""
        await self.continuity.write(entry)

    # ── 检索 ──────────────────────────────────────────────

    async def query(self, text: str, limit: int = 10) -> list[MemoryEntry]:
        """跨层检索：fact + continuity。

        返回结果按各层顺序拼接，总数不超过 limit。
        canon 为只读设定，overlay 目前无条目存储，不参与检索。
        """
        fact_results = await self.fact.query(text, limit=limit)
        continuity_results = await self.continuity.query(text, limit=limit)
        # 合并去重（按 id），保留 fact 优先
        seen: set[str] = set()
        merged: list[MemoryEntry] = []
        for entry in fact_results + continuity_results:
            if entry.id not in seen:
                seen.add(entry.id)
                merged.append(entry)
        return merged[:limit]

    # ── 遗忘 ──────────────────────────────────────────────

    async def forget(self, entry_id: str) -> None:
        """从所有可写层删除指定 id 的条目。"""
        await self.fact.forget(entry_id)
        await self.continuity.forget(entry_id)

    # ── 衰减 ──────────────────────────────────────────────

    async def decay(self, factor: float = 0.95) -> None:
        """对所有可衰减层执行衰减。"""
        await self.fact.decay(factor)
        await self.continuity.decay(factor)


# ── 全局单例 ──────────────────────────────────────────────────

_memory_manager_instance: Optional[UnifiedMemoryFacade] = None
_memory_manager_lock = threading.Lock()


def get_memory_manager() -> UnifiedMemoryFacade:
    """获取记忆门面单例（线程安全懒加载）。"""
    global _memory_manager_instance
    if _memory_manager_instance is None:
        with _memory_manager_lock:
            if _memory_manager_instance is None:
                _memory_manager_instance = UnifiedMemoryFacade()
    return _memory_manager_instance


__all__ = [
    "UnifiedMemoryFacade",
    "get_memory_manager",
]
