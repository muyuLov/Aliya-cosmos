"""测试因果推理引擎（causal.py）"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from agent.cognition.causal import CausalEngine, CausalGraph


class TestCausalGraph:
    def test_add_variable_and_relation(self):
        g = CausalGraph()
        g.add_variable("努力", 0.6)
        g.add_variable("成绩", 0.4)
        g.add_relation("努力", "成绩", strength=0.8)
        assert "努力" in g.variables
        assert len(g.relations) == 1

    def test_observe_updates_value(self):
        g = CausalGraph()
        g.add_variable("成绩", 0.4, confidence=0.5)
        g.observe("成绩", 0.9, confidence=0.9)
        assert g.get("成绩").value > 0.4

    def test_has_path(self):
        g = CausalGraph()
        g.add_relation("A", "B", strength=0.5)
        g.add_relation("B", "C", strength=0.5)
        assert g._has_path("A", "C") is True
        assert g._has_path("C", "A") is False

    def test_query_association_condition(self):
        g = CausalGraph()
        g.add_relation("努力", "成绩", strength=0.8)
        result = g.query_association("成绩", condition="努力")
        assert result["target"] == "成绩"
        assert result["condition"] == "努力"
        assert 0.0 <= result["value"] <= 1.0

    def test_query_association_no_condition(self):
        g = CausalGraph()
        g.add_variable("成绩", 0.7)
        result = g.query_association("成绩")
        assert result["value"] == pytest.approx(0.7)

    def test_intervened_copy_cuts_incoming_edges(self):
        g = CausalGraph()
        g.add_relation("A", "C", strength=0.8)
        g.add_relation("B", "C", strength=0.6)
        g2 = g.intervened_copy("C", 0.9)
        # C 的入边应被切断
        assert all(rel.target != "C" for rel in g2.relations)

    def test_predict_effect(self):
        g = CausalGraph()
        g.add_relation("关怀", "用户满意", strength=0.7)
        result = g.predict_effect("关怀", 1.0, "用户满意")
        assert result["intervention"] == "关怀"
        assert result["target"] == "用户满意"
        assert 0.0 <= result["value"] <= 1.0

    def test_predict_effect_missing_variable(self):
        g = CausalGraph()
        result = g.predict_effect("不存在", 1.0, "目标")
        assert result["value"] == 0.5

    def test_counterfactual(self):
        g = CausalGraph()
        g.add_relation("努力", "成绩", strength=0.8)
        result = g.counterfactual(
            "努力", actual_value=0.3, hypothetical_value=0.9, target="成绩"
        )
        assert result["target"] == "成绩"
        assert "difference" in result
        assert "explanation" in result

    def test_counterfactual_missing(self):
        g = CausalGraph()
        result = g.counterfactual("不存在", 0.5, 0.9, "目标")
        assert "error" in result

    def test_to_summary(self):
        g = CausalGraph()
        g.add_relation("A", "B", strength=0.7)
        summary = g.to_summary()
        assert "A" in summary
        assert "B" in summary


class TestCausalEngine:
    def test_observe(self):
        engine = CausalEngine()
        engine.observe("用户情绪", 0.8)
        assert engine.graph.get("用户情绪") is not None

    def test_build_from_world_model(self):
        from agent.cognition.world_model import WorldModel

        wm = WorldModel()
        wm.add_causal_link("用户表达情绪", "Agent 共情回应", probability=0.8)
        engine = CausalEngine()
        count = engine.build_from_world_model(wm)
        assert count == 1
        assert "用户表达情绪" in engine.graph.variables

    def test_build_from_world_model_none(self):
        engine = CausalEngine()
        assert engine.build_from_world_model(None) == 0

    def test_get_status(self):
        engine = CausalEngine()
        engine.graph.add_relation("A", "B", strength=0.5)
        status = engine.get_status()
        assert status["graph"]["variables"] == 2
        assert "summary" in status
