import pytest

from core.memory.layers import MemoryEntry
from core.memory.layers.continuity import ContinuityLayer


async def test_write_and_query_summaries():
    layer = ContinuityLayer()
    await layer.write(MemoryEntry(id="s1", content="在海獭号上与Kane讨论人造重力",
                                  source="scene", confidence=0.9, importance=0.7))
    await layer.write(MemoryEntry(id="s2", content="Ryoko 用元素周期表暗号确认记忆",
                                  source="scene", confidence=0.85, importance=0.6))
    entries = await layer.query("", limit=10)
    texts = [e.content for e in entries]
    assert any("人造重力" in t for t in texts)
    assert any("元素周期表" in t for t in texts)


async def test_query_returns_recent_first():
    layer = ContinuityLayer()
    await layer.write(MemoryEntry(id="s1", content="第一幕摘要", source="scene",
                                  confidence=0.5, importance=0.3))
    await layer.write(MemoryEntry(id="s2", content="第二幕摘要", source="scene",
                                  confidence=0.5, importance=0.3))
    entries = await layer.query("", limit=10)
    # 较新的写入应排在前面（近期优先）
    assert entries[0].content == "第二幕摘要"


async def test_forget_removes_entry():
    layer = ContinuityLayer()
    await layer.write(MemoryEntry(id="s1", content="将被遗忘", source="scene",
                                  confidence=0.5, importance=0.3))
    await layer.forget("s1")
    entries = await layer.query("", limit=10)
    assert entries == []


async def test_decay_reduces_confidence():
    layer = ContinuityLayer()
    await layer.write(MemoryEntry(id="s1", content="低置信条目", source="scene",
                                  confidence=0.8, importance=0.5))
    await layer.decay(factor=0.5)
    entries = await layer.query("", limit=10)
    assert entries[0].confidence == pytest.approx(0.4)
