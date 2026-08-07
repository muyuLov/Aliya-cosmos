"""测试世界模型（world_model.py）"""

from __future__ import annotations

import pytest

from agent.cognition.world_model import (
    Belief,
    EntityType,
    RelationType,
    WorldModel,
)


class TestBelief:
    def test_update_weights_average(self):
        belief = Belief(value="a", confidence=0.5)
        belief.update("a", confidence=0.9)
        assert belief.confidence == pytest.approx(0.5 * 0.5 + 0.5 * 0.9)

    def test_update_high_confidence_overwrites_value(self):
        belief = Belief(value="旧值", confidence=0.3)
        belief.update("新值", confidence=0.9)
        assert belief.value == "新值"
        assert belief.evidence_count == 2


class TestWorldModel:
    def test_add_entity(self):
        wm = WorldModel()
        eid = wm.add_entity("Aliya", EntityType.AGENT)
        entity = wm.get_entity(eid)
        assert entity is not None
        assert entity.name == "Aliya"
        assert entity.entity_type == EntityType.AGENT

    def test_add_entity_reuse_same_name(self):
        wm = WorldModel()
        eid1 = wm.add_entity("用户", EntityType.USER)
        eid2 = wm.add_entity("用户", EntityType.USER)
        assert eid1 == eid2

    def test_query_entities_by_type(self):
        wm = WorldModel()
        wm.add_entity("记忆", EntityType.CONCEPT)
        wm.add_entity("Aliya", EntityType.AGENT)
        concepts = wm.query_entities(entity_type=EntityType.CONCEPT)
        assert len(concepts) == 1

    def test_add_relation_and_find_related(self):
        wm = WorldModel()
        aliya = wm.add_entity("Aliya", EntityType.AGENT)
        mem = wm.add_entity("记忆", EntityType.CONCEPT)
        wm.add_relation(aliya, mem, RelationType.DEPENDS_ON, confidence=0.9)
        related = wm.find_related(aliya)
        assert len(related) == 1
        entity, rel = related[0]
        assert entity.name == "记忆"
        assert rel.relation_type == RelationType.DEPENDS_ON

    def test_add_causal_link_and_predict(self):
        wm = WorldModel()
        wm.add_causal_link("用户表达情绪", "Agent 共情回应", probability=0.8)
        result = wm.predict("用户表达情绪")
        assert len(result.steps) == 1
        assert result.final_state == "Agent 共情回应"
        assert 0.0 <= result.confidence <= 1.0

    def test_predict_no_match(self):
        wm = WorldModel()
        result = wm.predict("未知行动")
        assert result.steps == []
        assert result.final_state == "未知行动"

    def test_to_summary(self):
        wm = WorldModel()
        aliya = wm.add_entity("Aliya", EntityType.AGENT, properties={"age": 3}, salience=0.9)
        wm.add_entity("用户", EntityType.USER, salience=0.8)
        wm.add_relation(aliya, "用户", RelationType.PREFERS)
        summary = wm.to_summary()
        assert "Aliya" in summary
        assert "用户" in summary

    def test_get_stats(self):
        wm = WorldModel()
        wm.add_entity("a", EntityType.CONCEPT)
        wm.add_entity("b", EntityType.CONCEPT)
        stats = wm.get_stats()
        assert stats["entities"] == 2
        assert stats["relations"] == 0
