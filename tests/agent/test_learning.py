"""测试持续学习管道（learning.py）"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from agent.cognition.learning import (
    ConsolidationEngine,
    Experience,
    ExperienceReplay,
    LearningPipeline,
    PolicyLibrary,
)


class TestExperience:
    def test_priority_includes_surprise(self):
        exp = Experience(content="x", importance=0.5, surprise=0.5)
        assert exp.priority == pytest.approx(0.5 + 0.3 * 0.5)


class TestExperienceReplay:
    def test_add_and_len(self):
        replay = ExperienceReplay(capacity=10)
        replay.add(Experience(content="a", importance=0.5))
        assert len(replay) == 1

    def test_capacity_evicts_lowest_priority(self):
        replay = ExperienceReplay(capacity=3)
        for i in range(5):
            replay.add(Experience(content=f"e{i}", importance=0.1))
        replay.add(Experience(content="high", importance=1.0))
        assert len(replay) == 3
        assert "high" in [e.content for e in replay._experiences]

    def test_sample_deterministic(self):
        replay = ExperienceReplay()
        replay.add(Experience(content="a", importance=0.5))
        replay.add(Experience(content="b", importance=0.5))
        # rng 固定返回 0.0 → 命中第一条
        sampled = replay.sample(k=1, rng=lambda: 0.0)
        assert len(sampled) == 1
        assert sampled[0].content == "a"

    def test_sample_empty(self):
        replay = ExperienceReplay()
        assert replay.sample(k=3) == []

    def test_recent_failures(self):
        replay = ExperienceReplay()
        replay.add(Experience(content="ok", success=True, importance=0.5))
        replay.add(Experience(content="fail1", success=False, importance=0.5))
        replay.add(Experience(content="fail2", success=False, importance=0.5))
        fails = replay.recent_failures(limit=5)
        assert len(fails) == 2


class TestPolicyLibrary:
    def test_add_and_get(self):
        lib = PolicyLibrary()
        lib.add_policy("调试", "debugging", ["复现", "定位"])
        policy = lib.get("调试")
        assert policy is not None
        assert policy.used_count == 1
        assert policy.success_rate == 0.5

    def test_record_outcome_updates_rate(self):
        lib = PolicyLibrary()
        lib.add_policy("调试", "debugging", ["复现"])
        lib.record_outcome("调试", success=True)
        lib.record_outcome("调试", success=True)
        assert lib.get("调试").success_rate > 0.5

    def test_get_for_domain(self):
        lib = PolicyLibrary()
        lib.add_policy("a", "debugging", ["1"])
        lib.add_policy("b", "writing", ["2"])
        assert len(lib.get_for_domain("debugging")) == 1


class TestConsolidation:
    def test_consolidate_adds_policy(self):
        replay = ExperienceReplay()
        replay.add(Experience(content="成功经验", importance=1.0, success=True))
        lib = PolicyLibrary()
        engine = ConsolidationEngine(replay, lib)
        stats = engine.consolidate(min_priority=0.5)
        assert stats["policies_added"] >= 1
        assert stats["replayed"] >= 1

    def test_consolidate_writes_memory(self):
        class FakeMemory:
            def __init__(self):
                self.facts = {}

            def learn_fact(self, key, value, confidence, source):
                _ = (confidence, source)
                self.facts[key] = value

        replay = ExperienceReplay()
        replay.add(Experience(content="经验", importance=1.0, success=True))
        lib = PolicyLibrary()
        engine = ConsolidationEngine(replay, lib)
        mem = FakeMemory()
        stats = engine.consolidate(memory=mem, min_priority=0.5)
        assert stats["memories"] >= 1
        assert "experience:general" in mem.facts


class TestLearningPipeline:
    def test_record_and_replay(self):
        lp = LearningPipeline()
        lp.record(content="用户喜欢咖啡", domain="preference", importance=0.8)
        assert lp.get_status()["experiences"] == 1

    def test_consolidate_end_to_end(self):
        lp = LearningPipeline()
        lp.record(content="查询成功", domain="tools/query", success=True, importance=1.0)
        stats = lp.consolidate()
        assert stats["policies_added"] >= 1

    def test_get_status(self):
        lp = LearningPipeline()
        status = lp.get_status()
        assert "experiences" in status
        assert "policies" in status
