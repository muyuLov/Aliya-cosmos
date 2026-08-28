"""Task 2.7: UnifiedMemoryFacade 统一门面测试

验证四层记忆（Canon / Overlay / Continuity / Fact）通过统一门面访问，
以及 get_memory_manager() 返回新分层门面（非旧 GRAGMemoryManager）。
"""

import pytest
from core.memory.layers import MemoryEntry, MemoryLayer


# ── Step 1: 门面持有四层 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_facade_exposes_four_layers():
    """门面应持有 canon / overlay / continuity / fact 四层，且类型正确"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    assert hasattr(facade, "canon")
    assert hasattr(facade, "overlay")
    assert hasattr(facade, "continuity")
    assert hasattr(facade, "fact")
    assert isinstance(facade.canon, MemoryLayer)
    assert isinstance(facade.overlay, MemoryLayer)
    assert isinstance(facade.continuity, MemoryLayer)
    assert isinstance(facade.fact, MemoryLayer)
    assert facade.canon.name == "canon"
    assert facade.overlay.name == "overlay"
    assert facade.continuity.name == "continuity"
    assert facade.fact.name == "fact"


@pytest.mark.asyncio
async def test_facade_write_routes_to_fact():
    """通过 facade.write() 写入的条目应进入 fact 层"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    entry = MemoryEntry(
        id="f1", content="用户喜欢蓝色", source="conversation",
        confidence=0.9, importance=0.7,
    )
    await facade.write(entry)
    results = await facade.query("用户喜好")
    assert len(results) >= 1
    assert any("蓝色" in r.content for r in results)


@pytest.mark.asyncio
async def test_facade_write_routes_to_continuity():
    """通过 facade.write_continuity() 写入的条目应进入 continuity 层"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    entry = MemoryEntry(
        id="c1", content="场景：雨天咖啡馆对话", source="scene_summary",
        confidence=1.0, importance=0.6,
    )
    await facade.write_continuity(entry)
    results = await facade.continuity.query("", limit=5)
    assert len(results) >= 1
    assert any("咖啡馆" in r.content for r in results)


# ── Step 2: query() 跨层检索 ──────────────────────────────────


@pytest.mark.asyncio
async def test_facade_query_returns_across_layers():
    """query() 应跨 fact + continuity 两层检索（canon 只读，overlay 无条目）"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    await facade.write(MemoryEntry(
        id="f1", content="用户住在巴黎", source="conversation",
        confidence=0.85, importance=0.7,
    ))
    await facade.write_continuity(MemoryEntry(
        id="c1", content="场景：巴黎铁塔附近散步", source="scene",
        confidence=1.0, importance=0.6,
    ))
    results = await facade.query("巴黎")
    contents = [r.content for r in results]
    assert any("巴黎" in c for c in contents)


@pytest.mark.asyncio
async def test_facade_query_limit():
    """query(limit=N) 返回结果不超过 N 条"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    for i in range(10):
        await facade.write(MemoryEntry(
            id=f"f{i}", content=f"事实编号{i}", source="test",
            confidence=0.9, importance=0.5,
        ))
    results = await facade.query("任意", limit=3)
    assert len(results) <= 3


# ── Step 3: decay() 跨层衰减 ──────────────────────────────────


@pytest.mark.asyncio
async def test_facade_decay_reduces_confidence():
    """decay() 应降低 fact + continuity 层的 confidence"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    entry = MemoryEntry(
        id="f1", content="高置信度事实", source="test",
        confidence=1.0, importance=0.8,
    )
    await facade.write(entry)
    await facade.decay(factor=0.5)
    results = await facade.query("置信度")
    assert len(results) >= 1
    assert results[0].confidence == pytest.approx(0.5)


# ── Step 4: forget() 跨层删除 ──────────────────────────────────


@pytest.mark.asyncio
async def test_facade_forget_removes_entry():
    """forget(id) 应从对应层删除条目"""
    from core.memory.memory_manager import UnifiedMemoryFacade

    facade = UnifiedMemoryFacade()
    entry = MemoryEntry(
        id="target_to_forget", content="临时事实", source="test",
        confidence=0.9, importance=0.5,
    )
    await facade.write(entry)
    # 确认写入
    results = await facade.query("临时")
    assert len(results) >= 1
    # 遗忘
    await facade.forget("target_to_forget")
    # 验证删除
    fact_results = await facade.fact.query("", limit=100)
    assert not any(e.id == "target_to_forget" for e in fact_results)


# ── Step 5: get_memory_manager() 返回新门面 ────────────────────


def test_get_memory_manager_returns_facade():
    """get_memory_manager() 应返回 UnifiedMemoryFacade，而非旧 GRAGMemoryManager"""
    from core.memory.memory_manager import get_memory_manager, UnifiedMemoryFacade

    manager = get_memory_manager()
    assert isinstance(manager, UnifiedMemoryFacade)


# ── Step 6: 旧类 GRAGMemoryManager 不再存在 ─────────────────────


def test_old_grag_class_removed():
    """旧 GRAGMemoryManager 类不应再存在于 memory_manager 模块中"""
    from core.memory import memory_manager

    assert not hasattr(memory_manager, "GRAGMemoryManager")
