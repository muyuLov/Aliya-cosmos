"""core.memory 包

分层记忆系统公共接口。
"""

from core.memory.layers import MemoryEntry, MemoryLayer
from core.memory.layers.canon import CanonLayer
from core.memory.layers.overlay import OverlayLayer
from core.memory.layers.continuity import ContinuityLayer
from core.memory.layers.fact_layer import FactLayer
from core.memory.memory_manager import UnifiedMemoryFacade, get_memory_manager

__all__ = [
    # 公共类型
    "MemoryEntry",
    "MemoryLayer",
    # 四层实现
    "CanonLayer",
    "OverlayLayer",
    "ContinuityLayer",
    "FactLayer",
    # 门面
    "UnifiedMemoryFacade",
    "get_memory_manager",
]
