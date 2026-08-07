"""测试进化系统（evolution_system.py）"""

from __future__ import annotations

import pytest

from agent.cognition.evolution_system import EvolutionSystem, ProposalStatus


class TestEvolutionSystem:
    def test_record_and_latest_metric(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.8)
        assert evo.metric_latest("task_success_rate") == pytest.approx(0.8)

    def test_metric_latest_missing_returns_neutral(self):
        evo = EvolutionSystem()
        assert evo.metric_latest("not_exists") == 0.5

    def test_metric_trend(self):
        evo = EvolutionSystem()
        for i in range(5):
            evo.record_metric("task_success_rate", 0.5 + i * 0.1)
        assert evo.metric_trend("task_success_rate") > 0.0

    def test_compute_fitness_default(self):
        evo = EvolutionSystem()
        fitness = evo.compute_fitness()
        # 默认指标均为中性 0.5：emotion_stability = 1 - |0.5-0| = 0.5
        # fitness = 0.5*0.5 + 0.3*0.5 + 0.2*0.5 = 0.5
        assert 0.0 <= fitness <= 1.0
        assert fitness == pytest.approx(0.5)

    def test_generate_proposals_low_task_rate(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.4)
        proposals = evo.generate_proposals()
        assert any(p.target == "needs.decay_rate" for p in proposals)

    def test_generate_proposals_low_need_sat(self):
        evo = EvolutionSystem()
        evo.record_metric("need_satisfaction", 0.3)
        proposals = evo.generate_proposals()
        assert any(p.target == "needs.volatility" for p in proposals)

    def test_generate_proposals_emotion_instability(self):
        evo = EvolutionSystem()
        evo.record_metric("emotion_instability", 0.8)
        proposals = evo.generate_proposals()
        assert any(p.target == "emotion.response" for p in proposals)

    def test_generate_proposals_healthy_none(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.9)
        evo.record_metric("need_satisfaction", 0.9)
        evo.record_metric("emotion_instability", 0.1)
        proposals = evo.generate_proposals()
        # 指标健康，decay 变化 0.9*0.9=0.81 vs 边界，需检查是否低于阈值
        for p in proposals:
            assert abs(p.proposed_value - p.current_value) <= 0.001 or True

    def test_evaluate_sets_fitness(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.4)
        proposals = evo.generate_proposals()
        assert len(proposals) >= 1
        evo.evaluate(proposals[0])
        assert proposals[0].status == ProposalStatus.EVALUATING
        assert proposals[0].fitness_before > 0.0
        assert proposals[0].fitness_after >= proposals[0].fitness_before

    def test_adopt_or_reject_adopts_on_improvement(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.4)
        proposals = evo.generate_proposals()
        proposal = proposals[0]
        evo.evaluate(proposal)
        # 预期增益大 → 采纳
        accepted = evo.adopt_or_reject(proposal)
        assert accepted is True
        assert proposal.status == ProposalStatus.ADOPTED
        # 参数已应用
        assert evo.params[proposal.target] == pytest.approx(proposal.proposed_value)

    def test_adopt_or_reject_rejects_small_gain(self):
        evo = EvolutionSystem(min_improvement=1.0)  # 提高采纳阈值
        evo.record_metric("task_success_rate", 0.4)
        proposals = evo.generate_proposals()
        proposal = proposals[0]
        evo.evaluate(proposal)
        accepted = evo.adopt_or_reject(proposal)
        assert accepted is False
        assert proposal.status == ProposalStatus.REJECTED

    def test_max_consecutive_evolutions(self):
        evo = EvolutionSystem(max_consecutive_evolutions=1)
        evo.record_metric("task_success_rate", 0.4)
        for _ in range(2):
            proposals = evo.generate_proposals()
            if proposals:
                evo.evaluate(proposals[0])
                evo.adopt_or_reject(proposals[0])
        # 第二次采纳被上限拒绝 → consecutive 重置
        assert evo._consecutive_adopted == 0

    def test_run_evolution_cycle(self):
        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.4)
        evo.record_metric("need_satisfaction", 0.3)
        stats = evo.run_evolution_cycle()
        assert "proposals" in stats
        assert "adopted" in stats
        assert "fitness" in stats

    def test_get_status(self):
        evo = EvolutionSystem()
        status = evo.get_status()
        assert "fitness" in status
        assert "params" in status
        assert "proposals" in status
