"""测试元认知监控（meta_cognition.py）"""

from __future__ import annotations

from agent.cognition.meta_cognition import MetaCognitionMonitor, THINKING_MODES


class TestMetaCognitionMonitor:
    def test_track_decision(self):
        meta = MetaCognitionMonitor()
        meta.track_decision(domain="debugging", strategy="query", success=True)
        assert len(meta._decisions) == 1

    def test_no_bias_initially(self):
        meta = MetaCognitionMonitor()
        reports = meta.detect_biases()
        assert all(not r.active for r in reports)

    def test_confirmation_bias_detected(self):
        meta = MetaCognitionMonitor()
        for _ in range(5):
            meta.track_decision(domain="debugging", strategy="bad_strategy", success=False)
        reports = meta.detect_biases()
        confirm = [r for r in reports if r.name == "confirmation_bias"]
        assert confirm[0].active is True

    def test_overconfidence_detected(self):
        meta = MetaCognitionMonitor()
        for _ in range(10):
            meta.track_decision(domain="debugging", strategy="s", success=False, confidence=0.9)
        reports = meta.detect_biases()
        oc = [r for r in reports if r.name == "overconfidence"]
        assert oc[0].active is True

    def test_anchoring_detected(self):
        meta = MetaCognitionMonitor()
        for _ in range(8):
            meta.track_decision(domain="d", strategy="first", success=True)
        for _ in range(2):
            meta.track_decision(domain="d", strategy="other", success=True)
        reports = meta.detect_biases()
        anchor = [r for r in reports if r.name == "anchoring"]
        assert anchor[0].active is True

    def test_sunk_cost_detected(self):
        meta = MetaCognitionMonitor()
        for _ in range(4):
            meta.track_decision(domain="d", strategy="stuck", success=False)
        reports = meta.detect_biases()
        sunk = [r for r in reports if r.name == "sunk_cost"]
        assert sunk[0].active is True

    def test_overgeneralization_detected(self):
        meta = MetaCognitionMonitor()
        # 只尝试 1 次即视为可靠
        meta.track_decision(domain="d", strategy="single_shot", success=True)
        reports = meta.detect_biases()
        og = [r for r in reports if r.name == "overgeneralization"]
        assert og[0].active is True

    def test_recency_needs_samples(self):
        meta = MetaCognitionMonitor()
        for _ in range(5):
            meta.track_decision(domain="d", strategy="s", success=True)
        reports = meta.detect_biases()
        recency = [r for r in reports if r.name == "recency"]
        assert recency[0].active is False  # 样本不足

    def test_recommend_thinking_mode(self):
        meta = MetaCognitionMonitor()
        mode = meta.recommend_thinking_mode()
        assert mode in THINKING_MODES

    def test_recommend_reflective_on_overconfidence(self):
        meta = MetaCognitionMonitor()
        for _ in range(10):
            meta.track_decision(domain="d", strategy="s", success=False, confidence=0.9)
        assert meta.recommend_thinking_mode() == "reflective"

    def test_get_status(self):
        meta = MetaCognitionMonitor()
        meta.track_decision(domain="d", strategy="s", success=True)
        status = meta.get_status()
        assert status["decisions_tracked"] == 1
        assert "active_biases" in status
        assert "recommended_mode" in status

    def test_track_confidence(self):
        meta = MetaCognitionMonitor()
        meta.track_confidence(predicted=0.8, actual=0.5)
        assert len(meta._decisions) == 1
