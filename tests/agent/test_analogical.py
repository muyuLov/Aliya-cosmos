"""测试类比迁移引擎（analogical.py）"""

from __future__ import annotations

import pytest

from agent.cognition.analogical import AnalogicalEngine, NodeType, RelType, StructuralGraph


def _build_debugging_engine() -> AnalogicalEngine:
    """构建一个含调试领域结构的引擎（带节点类型，匹配 debugging 模式）。"""
    engine = AnalogicalEngine()
    engine.encode_domain(
        "调试",
        [
            ("症状", "原因", "causes", "problem", "cause"),
            ("原因", "工具", "requires", "cause", "tool"),
            ("工具", "症状", "mitigates", "tool", "problem"),
        ],
    )
    return engine


class TestStructuralGraph:
    def test_add_node_and_relation(self):
        g = StructuralGraph(domain="测试")
        g.add_relation(
            "问题", "原因", RelType.CAUSES,
            source_type=NodeType.PROBLEM, target_type=NodeType.CAUSE,
        )
        assert g.node_count() == 2
        assert len(g.relations) == 1

    def test_abstract_skeleton(self):
        g = StructuralGraph(domain="测试")
        g.add_relation(
            "问题", "原因", RelType.CAUSES,
            source_type=NodeType.PROBLEM, target_type=NodeType.CAUSE,
        )
        skeleton = g.abstract()
        assert (NodeType.PROBLEM, RelType.CAUSES, NodeType.CAUSE) in skeleton


class TestAnalogicalEngine:
    def test_encode_domain(self):
        engine = _build_debugging_engine()
        graph = engine.get_domain("调试")
        assert graph is not None
        assert graph.node_count() == 3

    def test_match_pattern_debugging(self):
        engine = _build_debugging_engine()
        pattern, score = engine.match_pattern("调试")
        assert pattern == "debugging"
        assert score > 0.5

    def test_structural_similarity_identical(self):
        engine = AnalogicalEngine()
        engine.encode_domain("A", [("x", "y", "causes", "problem", "cause")])
        engine.encode_domain("B", [("m", "n", "causes", "problem", "cause")])
        assert engine.structural_similarity("A", "B") == pytest.approx(1.0)

    def test_structural_similarity_disjoint(self):
        engine = AnalogicalEngine()
        engine.encode_domain("A", [("x", "y", "causes", "problem", "cause")])
        engine.encode_domain("B", [("m", "n", "blocks", "problem", "goal")])
        assert engine.structural_similarity("A", "B") == 0.0

    def test_query_analogies(self):
        engine = AnalogicalEngine()
        engine.encode_domain(
            "调试",
            [("s", "c", "causes", "problem", "cause"),
             ("t", "s", "mitigates", "tool", "problem")],
        )
        engine.encode_domain(
            "优化",
            [("p", "c", "causes", "problem", "cause"),
             ("t", "p", "mitigates", "tool", "problem")],
        )
        engine.encode_domain("无关", [("a", "b", "part_of", "goal", "goal")])
        analogies = engine.query_analogies("调试", limit=3)
        assert any(d["domain"] == "优化" for d in analogies)

    def test_transfer_with_strategies(self):
        engine = AnalogicalEngine()
        engine.encode_domain(
            "调试",
            [
                ("症状", "原因", "causes", "problem", "cause"),
                ("原因", "工具", "requires", "cause", "tool"),
                ("工具", "症状", "mitigates", "tool", "problem"),
            ],
        )
        engine.encode_domain(
            "优化",
            [
                ("瓶颈", "根因", "causes", "problem", "cause"),
                ("根因", "参数", "requires", "cause", "tool"),
                ("参数", "瓶颈", "mitigates", "tool", "problem"),
            ],
        )
        result = engine.transfer("调试", "优化", "性能瓶颈")
        assert result["confidence"] > 0.0
        assert any("性能瓶颈" in a for a in result["advice"])

    def test_transfer_missing_domain(self):
        engine = AnalogicalEngine()
        result = engine.transfer("不存在", "优化", "问题")
        assert result["advice"] == []
        assert result["confidence"] == 0.0

    def test_to_summary(self):
        engine = _build_debugging_engine()
        summary = engine.to_summary()
        assert "调试" in summary

    def test_get_status(self):
        engine = _build_debugging_engine()
        status = engine.get_status()
        assert status["domain_count"] == 1
        assert "调试" in status["domains"]
