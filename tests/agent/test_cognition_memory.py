"""测试五层层次化记忆系统（memory.py）"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from agent.cognition.memory import (
    EpisodicMemory,
    HierarchicalMemory,
    ProceduralMemory,
    SemanticMemory,
    WorkingMemory,
)


class TestWorkingMemory:
    def test_attend_and_recall(self):
        wm = WorkingMemory()
        wm.attend("用户喜欢喝咖啡", weight=1.0)
        wm.attend("用户住在北京", weight=0.8)
        recalled = wm.recall()
        assert "用户喜欢喝咖啡" in recalled
        assert "用户住在北京" in recalled

    def test_capacity_limit(self):
        wm = WorkingMemory(capacity=3)
        for i in range(5):
            wm.attend(f"chunk{i}", weight=0.5)
        assert len(wm) <= 3

    def test_high_weight_survives_eviction(self):
        wm = WorkingMemory(capacity=3)
        wm.attend("重要信息", weight=1.0)
        for i in range(5):
            wm.attend(f"chunk{i}", weight=0.1)
        assert "重要信息" in wm.recall()

    def test_reattend_boosts_weight(self):
        wm = WorkingMemory()
        wm.attend("内容", weight=0.3)
        wm.attend("内容", weight=1.0)
        # 最高注意力权重者优先召回
        assert wm.recall(limit=1) == ["内容"]


class TestEpisodicMemory:
    def test_remember_and_recall(self):
        em = EpisodicMemory()
        em.remember("第一次见到用户", importance=0.9, context="meeting")
        records = em.recall(limit=5)
        assert len(records) == 1
        assert records[0].content == "第一次见到用户"
        assert records[0].context == "meeting"

    def test_recall_sorted_by_importance(self):
        em = EpisodicMemory()
        em.remember("低重要性", importance=0.2)
        em.remember("高重要性", importance=0.9)
        records = em.recall(limit=5)
        assert records[0].content == "高重要性"

    def test_min_importance_filter(self):
        em = EpisodicMemory()
        em.remember("低", importance=0.1)
        em.remember("高", importance=0.8)
        records = em.recall(limit=5, min_importance=0.5)
        assert len(records) == 1
        assert records[0].content == "高"

    def test_capacity(self):
        em = EpisodicMemory(capacity=3)
        for i in range(5):
            em.remember(f"event{i}", importance=0.5)
        assert len(em) == 3


class TestSemanticMemory:
    def test_learn_and_recall(self):
        sm = SemanticMemory()
        sm.learn("用户偏好", "喜欢咖啡", confidence=0.8)
        fact = sm.recall("用户偏好")
        assert fact is not None
        assert fact.value == "喜欢咖啡"
        assert fact.confidence == pytest.approx(0.8)

    def test_bayesian_update_increases_confidence(self):
        sm = SemanticMemory()
        sm.learn("用户偏好", "喜欢咖啡", confidence=0.5)
        sm.learn("用户偏好", "喜欢咖啡", confidence=0.9)
        fact = sm.recall("用户偏好")
        # 0.5*0.5 + 0.5*0.9 = 0.7
        assert fact.confidence == pytest.approx(0.7)

    def test_search_substring(self):
        sm = SemanticMemory()
        sm.learn("用户偏好", "喜欢咖啡", confidence=0.9)
        sm.learn("天气", "今天下雨", confidence=0.8)
        results = sm.search("咖啡")
        assert len(results) == 1
        assert results[0].key == "用户偏好"

    def test_search_sorted_by_confidence(self):
        sm = SemanticMemory()
        sm.learn("a", "hello world", confidence=0.6)
        sm.learn("b", "hello cosmos", confidence=0.9)
        results = sm.search("hello")
        assert results[0].key == "b"


class TestProceduralMemory:
    def test_learn_and_recall(self):
        pm = ProceduralMemory()
        pm.learn("调试流程", ["复现", "定位", "修复"], domain="debugging", success_rate=0.8)
        skill = pm.recall("调试流程")
        assert skill is not None
        assert skill.steps == ["复现", "定位", "修复"]
        assert skill.used_count == 1

    def test_recall_missing_returns_none(self):
        pm = ProceduralMemory()
        assert pm.recall("不存在") is None

    def test_skills_for_domain(self):
        pm = ProceduralMemory()
        pm.learn("a", ["1"], domain="debugging")
        pm.learn("b", ["2"], domain="writing")
        assert len(pm.skills_for_domain("debugging")) == 1


class TestHierarchicalMemory:
    def test_attend_and_build_context(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.attend("用户提到喜欢吃甜食")
        mem.learn_fact("用户偏好", "喜欢甜食", confidence=0.9)
        ctx = mem.build_context(query="甜食")
        assert any("喜欢甜食" in part for part in ctx)

    def test_consolidate_working_to_episodic(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.attend("重要信息", weight=1.0)
        stats = mem.consolidate()
        assert stats["episodes"] >= 1

    def test_consolidate_boosts_evidence(self):
        mem = HierarchicalMemory(enable_vector=False)
        for _ in range(5):
            mem.learn_fact("事实", "值", confidence=0.5)
        stats = mem.consolidate()
        assert stats["facts"] >= 1
        fact = mem.semantic.recall("事实")
        assert fact.confidence > 0.5

    def test_get_stats(self):
        mem = HierarchicalMemory(enable_vector=False)
        stats = mem.get_stats()
        assert "working" in stats
        assert "semantic" in stats
        assert "vector" in stats

    def test_vector_disabled_graceful(self):
        mem = HierarchicalMemory(enable_vector=False)
        assert mem.vector.enabled is False

    async def test_vector_add_when_disabled(self):
        mem = HierarchicalMemory(enable_vector=False)
        result = await mem.vector_add("测试")
        assert result is False
