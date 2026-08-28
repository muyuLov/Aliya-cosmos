import pytest

from core.memory.layers import MemoryEntry
from core.memory.layers.fact_layer import FactLayer


async def test_write_and_query_facts():
    layer = FactLayer()
    await layer.write(MemoryEntry(id="f1", content="Aliya 是海獭号生物学家",
                                  source="extract", confidence=0.9, importance=0.8))
    await layer.write(MemoryEntry(id="f2", content="Kane 喜欢读哲学书",
                                  source="extract", confidence=0.8, importance=0.6))
    entries = await layer.query("", limit=10)
    texts = [e.content for e in entries]
    assert any("生物学家" in t for t in texts)
    assert any("哲学书" in t for t in texts)


async def test_query_ranked_by_weighted_score():
    layer = FactLayer()
    # 高重要高置信的应排在前面
    await layer.write(MemoryEntry(id="f1", content="低分事实", source="extract",
                                  confidence=0.5, importance=0.2))
    await layer.write(MemoryEntry(id="f2", content="高分事实", source="extract",
                                  confidence=0.95, importance=0.9))
    entries = await layer.query("", limit=10)
    assert entries[0].content == "高分事实"


async def test_forget_removes_fact():
    layer = FactLayer()
    await layer.write(MemoryEntry(id="f1", content="将被遗忘的事实", source="extract",
                                  confidence=0.8, importance=0.7))
    await layer.forget("f1")
    entries = await layer.query("", limit=10)
    assert entries == []


async def test_decay_reduces_confidence():
    layer = FactLayer()
    await layer.write(MemoryEntry(id="f1", content="衰减事实", source="extract",
                                  confidence=0.9, importance=0.6))
    await layer.decay(factor=0.8)
    entries = await layer.query("", limit=10)
    assert entries[0].confidence == pytest.approx(0.72)
