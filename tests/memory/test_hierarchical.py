"""测试五层层次化记忆系统（hierarchical.py）"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import time

import pytest

from core.memory.hierarchical import (
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
        assert mem._vector_enabled is False
        assert mem._vector_ready is False

    @pytest.mark.asyncio
    async def test_vector_add_disabled_no_crash(self):
        """向量关闭时写入操作不崩溃（自动降级）。"""
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_fact("key", "value", confidence=0.8)
        mem.remember_episode("episode content", importance=0.9)
        assert len(mem._pending_sync) == 0  # 不会排入队列

    @pytest.mark.asyncio
    async def test_build_context_async_no_vector(self):
        """关闭向量时 build_context_async 也能正常工作（仅各层召回）。"""
        mem = HierarchicalMemory(enable_vector=False)
        mem.attend("当前任务")
        mem.learn_fact("事实", "值", confidence=0.9)
        ctx = await mem.build_context_async(query="事实", limit=5)
        assert any("事实" in part for part in ctx)
        assert any("值" in part for part in ctx)

    @pytest.mark.asyncio
    async def test_sync_to_vector_queues_pending(self):
        """_sync_to_vector 在向量可用时应排入待同步队列。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True  # 模拟已就绪
        mem._vector_store = _MockVectorStore()
        mem._sync_to_vector("test content", {"layer": "semantic"})
        assert len(mem._pending_sync) == 1
        assert mem._pending_sync[0][0] == "test content"
        assert mem._pending_sync[0][1] == {"layer": "semantic"}
        # drain 后队列清空
        await mem._drain_sync()
        assert len(mem._pending_sync) == 0

    @pytest.mark.asyncio
    async def test_learn_fact_auto_sync(self):
        """learn_fact 自动触发向量同步。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        mem.learn_fact("用户偏好", "喜欢咖啡", confidence=0.8)
        assert len(mem._pending_sync) == 1
        text, meta = mem._pending_sync[0]
        assert "用户偏好" in text
        assert "喜欢咖啡" in text
        assert meta["layer"] == "semantic"

    @pytest.mark.asyncio
    async def test_remember_episode_auto_sync(self):
        """remember_episode 根据重要性自动触发向量同步。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        # 高重要性 → 同步
        mem.remember_episode("重要事件", importance=0.9)
        assert len(mem._pending_sync) == 1
        # 低重要性 → 不同步
        mem.remember_episode("琐事", importance=0.1)
        assert len(mem._pending_sync) == 1  # 不变

    @pytest.mark.asyncio
    async def test_learn_skill_auto_sync(self):
        """learn_skill 自动触发向量同步。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        mem.learn_skill("调试流程", ["复现", "定位", "修复"], domain="dev")
        assert len(mem._pending_sync) == 1
        text = mem._pending_sync[0][0]
        assert "调试流程" in text
        assert "复现→定位→修复" in text


class TestCollectEntityMemoryAttrs:
    """实体五层记忆属性聚合（供图节点挂载）"""

    def test_empty_when_no_match(self):
        mem = HierarchicalMemory(enable_vector=False)
        assert mem.collect_entity_memory_attrs("咖啡") == {}

    def test_empty_when_blank_name(self):
        mem = HierarchicalMemory(enable_vector=False)
        assert mem.collect_entity_memory_attrs("") == {}
        assert mem.collect_entity_memory_attrs("  ") == {}

    def test_collects_semantic_confidence(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_fact("用户偏好", "喜欢咖啡", confidence=0.8)
        attrs = mem.collect_entity_memory_attrs("咖啡")
        assert "semantic" in attrs["layers"]
        assert attrs["confidence"] == pytest.approx(0.8)

    def test_collects_working_and_episodic(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.attend("正在讨论咖啡", weight=1.0)
        mem.remember_episode("用户说喜欢咖啡", importance=0.9)
        attrs = mem.collect_entity_memory_attrs("咖啡")
        assert "working" in attrs["layers"]
        assert "episodic" in attrs["layers"]
        assert attrs["attention_weight"] == pytest.approx(1.0)
        assert attrs["importance"] == pytest.approx(0.9)

    def test_collects_procedural_success_rate(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_skill("咖啡冲煮", ["磨豆", "注水"], domain="coffee", success_rate=0.9)
        attrs = mem.collect_entity_memory_attrs("咖啡")
        assert "procedural" in attrs["layers"]
        assert attrs["success_rate"] == pytest.approx(0.9)

    def test_attrs_schema(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_fact("主题", "咖啡", confidence=0.7)
        mem.remember_episode("买了咖啡豆", importance=0.6)
        attrs = mem.collect_entity_memory_attrs("咖啡")
        for key in (
            "layers", "importance", "confidence", "success_rate",
            "attention_weight", "access_count", "heat",
        ):
            assert key in attrs
        assert attrs["access_count"] >= 0
        assert "episodic" in attrs["layers"]
        assert "semantic" in attrs["layers"]


class TestApplyForgetting:
    """统一遗忘机制：各层数值随未访问时长永久衰减，低于阈值被清理"""

    def test_episodic_importance_decays(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.remember_episode("事件", importance=0.9)
        rec = mem.episodic._records[0]
        # 1 天前访问（半衰期 1 天）→ importance 衰减到 0.45
        rec.last_access_at = time.time() - 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["episodic_decayed"] == 1
        assert rec.importance == pytest.approx(0.45, abs=0.01)

    def test_episodic_forgotten_below_threshold(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.remember_episode("琐事", importance=0.2)
        rec = mem.episodic._records[0]
        # 4 天前访问 → 0.2 * 2^-4 = 0.0125 < 0.1 被清理
        rec.last_access_at = time.time() - 4 * 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["episodic_forgotten"] == 1
        assert len(mem.episodic) == 0

    def test_semantic_confidence_decays(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_fact("事实", "值", confidence=0.8)
        fact = list(mem.semantic.iter_all())[0]
        # 7 天前访问（长期半衰期 7 天）→ confidence 衰减到 0.4
        fact.last_access_at = time.time() - 7 * 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["semantic_decayed"] == 1
        assert fact.confidence == pytest.approx(0.4, abs=0.01)

    def test_semantic_forgotten_below_threshold(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_fact("事实", "值", confidence=0.2)
        fact = list(mem.semantic.iter_all())[0]
        # 14 天前访问 → 0.2 * 2^-2 = 0.05 < 0.1 被清理
        fact.last_access_at = time.time() - 14 * 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["semantic_forgotten"] == 1
        assert len(mem.semantic) == 0

    def test_procedural_success_rate_decays(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.learn_skill("技能", ["步骤"], domain="d", success_rate=0.9)
        skill = list(mem.procedural.iter_all())[0]
        skill.last_access_at = time.time() - 7 * 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["procedural_decayed"] == 1
        assert skill.success_rate == pytest.approx(0.45, abs=0.01)

    def test_meta_heat_decays(self):
        mem = HierarchicalMemory(enable_vector=False)
        mem.meta.touch("主题", "semantic", hit=True)  # heat = 0.6
        rec = mem.meta.get("主题", "semantic")
        # 30 天前访问（元记忆半衰期 30 天）→ heat 衰减到 0.3
        rec.last_access_at = time.time() - 30 * 24 * 60 * 60
        stats = mem.apply_forgetting()
        assert stats["meta_decayed"] == 1
        assert rec.heat == pytest.approx(0.3, abs=0.01)

    def test_returns_full_stats_schema(self):
        mem = HierarchicalMemory(enable_vector=False)
        stats = mem.apply_forgetting()
        for key in (
            "episodic_decayed", "episodic_forgotten",
            "semantic_decayed", "semantic_forgotten",
            "procedural_decayed", "procedural_forgotten",
            "meta_decayed", "meta_forgotten",
        ):
            assert key in stats

    def test_idempotent_on_empty(self):
        mem = HierarchicalMemory(enable_vector=False)
        stats = mem.apply_forgetting()
        assert all(stats[key] == 0 for key in stats)

    @pytest.mark.asyncio
    async def test_forgetting_purges_vector_semantic(self):
        """被遗忘的语义记忆同步从向量索引移除（按 metadata key 定位）。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        mem.learn_fact("过时事实", "旧值", confidence=0.2)
        mem.learn_fact("保留事实", "新值", confidence=0.9)
        await mem._drain_sync()
        assert len(mem._vector_store) == 2

        fact = mem.semantic.recall("过时事实")
        fact.last_access_at = time.time() - 14 * 24 * 60 * 60  # 置信度 0.2→0.05

        stats = mem.apply_forgetting()
        assert stats["semantic_forgotten"] == 1
        assert stats["vector_purged"] == 1
        assert len(mem._vector_store) == 1  # 仅保留"保留事实"
        # 未被遗忘的向量条目仍在
        assert mem._vector_store.find_ids(text="保留事实: 新值")

    @pytest.mark.asyncio
    async def test_forgetting_purges_vector_episodic_and_procedural(self):
        """情景（按文本）与程序（按 metadata name）被遗忘时向量联动清理。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        mem.remember_episode("将被遗忘的事件", importance=0.9)
        mem.learn_skill("旧技能", ["步骤A"], domain="dev", success_rate=0.2)
        mem.remember_episode("保留事件", importance=0.9)
        await mem._drain_sync()
        assert len(mem._vector_store) == 3

        ep = mem.episodic._records[0]
        ep.last_access_at = time.time() - 4 * 24 * 60 * 60  # importance 0.9→0.056
        skill = list(mem.procedural.iter_all())[0]
        skill.last_access_at = time.time() - 14 * 24 * 60 * 60  # 成功率 0.2→0.05

        stats = mem.apply_forgetting()
        assert stats["vector_purged"] == 2
        assert len(mem._vector_store) == 1  # 仅"保留事件"（id-1）
        assert mem._vector_store.find_ids(text="保留事件")

    @pytest.mark.asyncio
    async def test_forgetting_cleans_pending_sync(self):
        """未入库（pending 队列）的被遗忘条目不进入向量存储。"""
        mem = HierarchicalMemory(enable_vector=True)
        mem._vector_ready = True
        mem._vector_store = _MockVectorStore()
        mem.learn_fact("过时事实", "旧值", confidence=0.2)  # 未 drain，仍在 pending
        mem.remember_episode("将被遗忘的事件", importance=0.9)  # 未 drain
        assert len(mem._vector_store) == 0
        assert len(mem._pending_sync) == 2

        fact = mem.semantic.recall("过时事实")
        fact.last_access_at = time.time() - 14 * 24 * 60 * 60
        ep = mem.episodic._records[0]
        ep.last_access_at = time.time() - 4 * 24 * 60 * 60

        stats = mem.apply_forgetting()
        assert stats["vector_purged"] == 2  # 从 pending 队列清理，未入库
        assert len(mem._pending_sync) == 0
        assert len(mem._vector_store) == 0  # 未写入向量库

    def test_vector_purged_key_in_stats(self):
        """遗忘统计始终包含 vector_purged 键（向量关闭时为 0）。"""
        mem = HierarchicalMemory(enable_vector=False)
        stats = mem.apply_forgetting()
        assert "vector_purged" in stats
        assert stats["vector_purged"] == 0


class _MockVectorStore:
    """模拟向量存储（不触发真实 Milvus/embedding 调用）。"""

    def __init__(self):
        self.items: dict[str, tuple[str, dict]] = {}
        self._next = 0

    async def add(self, text: str, metadata: dict | None = None) -> str:
        iid = f"id-{self._next}"
        self._next += 1
        self.items[iid] = (text, metadata or {})
        return iid

    def find_ids(
        self, text: str | None = None, metadata: dict | None = None
    ) -> list[str]:
        return [
            iid
            for iid, (t, m) in self.items.items()
            if (text is None or t == text)
            and (metadata is None or all(m.get(k) == v for k, v in metadata.items()))
        ]

    def delete_many(self, item_ids: list[str]) -> int:
        deleted = 0
        for iid in item_ids:
            if iid in self.items:
                del self.items[iid]
                deleted += 1
        return deleted

    def __len__(self) -> int:
        return len(self.items)
