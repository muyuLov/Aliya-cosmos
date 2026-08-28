"""分层记忆协议：统一 MemoryEntry 结构与 MemoryLayer 层接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    content: str
    source: str
    confidence: float
    importance: float
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryLayer(ABC):
    name: str = "layer"

    @abstractmethod
    async def write(self, entry: MemoryEntry) -> None: ...

    @abstractmethod
    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]: ...

    @abstractmethod
    async def forget(self, entry_id: str) -> None: ...

    @abstractmethod
    async def decay(self, factor: float = 0.95) -> None: ...
