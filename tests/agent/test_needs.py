"""测试 PSI 需求驱动系统（needs.py）"""

from __future__ import annotations

import pytest

from agent.cognition.needs import Need, NeedDriveSystem, NeedType


class TestNeed:
    def test_default_state(self):
        need = Need(name=NeedType.CERTAINTY)
        assert need.current_level == 0.6
        assert need.target_level == 0.8
        assert need.satisfaction == pytest.approx(0.75)

    def test_satisfy_increases_level(self):
        need = Need(name=NeedType.COMPETENCE, current_level=0.5)
        need.satisfy(0.2)
        assert need.current_level == pytest.approx(0.7)

    def test_satisfy_caps_at_one(self):
        need = Need(name=NeedType.COMPETENCE, current_level=0.9)
        need.satisfy(0.5)
        assert need.current_level == 1.0

    def test_tick_decays(self):
        need = Need(name=NeedType.ENERGY, current_level=0.6, decay_rate=0.02)
        # rng 固定返回 0.5 → 无噪声
        need.tick(dt=1.0, rng=lambda: 0.5)
        assert need.current_level == pytest.approx(0.6 - 0.02)

    def test_deficit(self):
        need = Need(name=NeedType.CERTAINTY, current_level=0.4)
        assert need.deficit == pytest.approx(0.8 - 0.4)

    def test_deficit_never_negative(self):
        need = Need(name=NeedType.CERTAINTY, current_level=0.95)
        assert need.deficit == 0.0


class TestNeedDriveSystem:
    def test_has_five_needs(self):
        nds = NeedDriveSystem()
        assert set(nds.needs.keys()) == set(NeedType)

    def test_record_tool_success_boosts_competence(self):
        nds = NeedDriveSystem()
        comp_before = nds.needs[NeedType.COMPETENCE].current_level
        nds.record_tool_result(success=True)
        assert nds.needs[NeedType.COMPETENCE].current_level > comp_before

    def test_record_tool_failure_decreases_certainty(self):
        nds = NeedDriveSystem()
        cert_before = nds.needs[NeedType.CERTAINTY].current_level
        nds.record_tool_result(success=False)
        assert nds.needs[NeedType.CERTAINTY].current_level < cert_before

    def test_record_interaction_boosts_relatedness(self):
        nds = NeedDriveSystem()
        rel_before = nds.needs[NeedType.RELATEDNESS].current_level
        nds.record_user_interaction(positive=True)
        assert nds.needs[NeedType.RELATEDNESS].current_level > rel_before

    def test_energy_consumption_decreases_energy(self):
        nds = NeedDriveSystem()
        en_before = nds.needs[NeedType.ENERGY].current_level
        nds.record_energy_consumption(1000)
        assert nds.needs[NeedType.ENERGY].current_level < en_before

    def test_autonomy_action_boosts_autonomy(self):
        nds = NeedDriveSystem()
        auto_before = nds.needs[NeedType.AUTONOMY].current_level
        nds.record_autonomy_action()
        assert nds.needs[NeedType.AUTONOMY].current_level > auto_before

    def test_tick_all_needs(self):
        nds = NeedDriveSystem()
        nds.tick(dt=1.0)
        for need in nds.needs.values():
            assert 0.0 <= need.current_level <= 1.0

    def test_compute_drive_vector(self):
        nds = NeedDriveSystem()
        vector = nds.compute_drive_vector()
        assert set(vector.keys()) == {nt.value for nt in NeedType}
        assert all(v >= 0.0 for v in vector.values())

    def test_dynamic_alpha_bounds(self):
        nds = NeedDriveSystem()
        alpha = nds.compute_dynamic_alpha()
        assert 0.3 <= alpha <= 0.9

    def test_emotion_gradient_shape(self):
        nds = NeedDriveSystem()
        gradient = nds.compute_emotion_gradient()
        assert set(gradient.keys()) == {"valence", "arousal", "dominance"}
        assert -1.0 <= gradient["valence"] <= 1.0
        assert 0.0 <= gradient["arousal"] <= 1.0
        assert 0.0 <= gradient["dominance"] <= 1.0

    def test_emotion_gradient_valence_reflects_satisfaction(self):
        nds = NeedDriveSystem()
        # 全部满足 → valence 应接近 1
        for need in nds.needs.values():
            need.current_level = 1.0
        gradient = nds.compute_emotion_gradient()
        assert gradient["valence"] == pytest.approx(2.0 * 1.0 - 1.0, abs=1e-6)

    def test_emotion_gradient_dominance_reflects_success_rate(self):
        nds = NeedDriveSystem()
        nds.record_tool_result(success=True)
        nds.record_tool_result(success=True)
        gradient = nds.compute_emotion_gradient()
        assert gradient["dominance"] == pytest.approx(0.2 + 0.8 * 1.0)

    def test_intrinsic_reward(self):
        nds = NeedDriveSystem()
        reward = nds.compute_intrinsic_reward()
        assert -1.0 <= reward <= 1.0

    def test_get_status(self):
        nds = NeedDriveSystem()
        status = nds.get_status()
        assert "needs" in status
        assert "emotion_gradient" in status
        assert "alpha" in status
        assert status["task_success"] == 0


class TestNeedDriveSystemDeterministic:
    def test_deterministic_rng(self):
        """注入确定性随机源，tick 结果可复现。"""
        nds = NeedDriveSystem(rng=lambda: 0.5)
        before = {nt: n.current_level for nt, n in nds.needs.items()}
        nds.tick(dt=1.0)
        after = {nt: n.current_level for nt, n in nds.needs.items()}
        # rng=0.5 → 噪声为 0，只有衰减
        for nt in NeedType:
            assert after[nt] == pytest.approx(before[nt] - nds.needs[nt].decay_rate)
