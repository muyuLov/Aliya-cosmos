"""测试自主性引擎（autonomy.py）"""

from __future__ import annotations

import pytest

from agent.cognition.autonomy import ActionProposal, AutonomyEngine, GoalStatus, Priority


class TestGoal:
    def test_progress_to_complete(self):
        engine = AutonomyEngine()
        gid = engine.add_goal("学习新技能", priority=Priority.HIGH)
        goal = engine.get_goal(gid)
        assert goal is not None
        goal.report_progress(0.6)
        assert goal.progress == pytest.approx(0.6)
        goal.report_progress(0.5)
        assert goal.status == GoalStatus.COMPLETED


class TestAutonomyEngine:
    def test_add_goal(self):
        engine = AutonomyEngine()
        gid = engine.add_goal("熟悉用户", description="了解用户喜好")
        assert gid != ""
        goal = engine.get_goal(gid)
        assert goal is not None
        assert goal.title == "熟悉用户"
        assert goal.status == GoalStatus.ACTIVE

    def test_max_active_goals(self):
        engine = AutonomyEngine(max_active_goals=2)
        engine.add_goal("目标1")
        engine.add_goal("目标2")
        gid = engine.add_goal("目标3")
        assert gid == ""  # 超限拒绝

    def test_report_progress(self):
        engine = AutonomyEngine()
        gid = engine.add_goal("目标")
        engine.report_progress(gid, delta=0.3)
        goal = engine.get_goal(gid)
        assert goal is not None
        assert goal.progress == pytest.approx(0.3)

    def test_abandon_goal(self):
        engine = AutonomyEngine()
        gid = engine.add_goal("目标")
        engine.abandon_goal(gid)
        goal = engine.get_goal(gid)
        assert goal is not None
        assert goal.status == GoalStatus.ABANDONED

    def test_detect_stagnation(self):
        engine = AutonomyEngine(stagnation_threshold=60.0)
        gid = engine.add_goal("停滞目标")
        # 将最近进度时间设为过去，触发停滞检测
        goal = engine.get_goal(gid)
        assert goal is not None
        goal.last_progress_at = 0.0
        stagnant = engine.detect_stagnation()
        assert gid in stagnant

    def test_generate_proposals_for_stagnant(self):
        engine = AutonomyEngine(stagnation_threshold=60.0)
        gid = engine.add_goal("停滞目标", priority=Priority.HIGH)
        goal = engine.get_goal(gid)
        assert goal is not None
        goal.last_progress_at = 0.0
        engine.detect_stagnation()
        proposals = engine.generate_proposals()
        assert len(proposals) >= 1
        assert any(p.related_goal_id == gid for p in proposals)

    def test_generate_proposals_for_autonomy_deficit(self):
        engine = AutonomyEngine()
        proposals = engine.generate_proposals(need_deficits={"autonomy": 0.5})
        assert any(p.related_need == "autonomy" for p in proposals)

    def test_mark_executed(self):
        engine = AutonomyEngine()
        gid = engine.add_goal("目标")
        engine.report_progress(gid, delta=0.4)
        proposal = ActionProposal(
            action="推进目标「目标」",
            reason="测试",
            related_goal_id=gid,
            priority=Priority.MEDIUM,
        )
        engine.mark_executed(proposal)
        assert engine._executed_actions == ["推进目标「目标」"]
        goal = engine.get_goal(gid)
        assert goal is not None
        assert goal.progress == pytest.approx(0.4 + 0.2)

    def test_get_status(self):
        engine = AutonomyEngine()
        engine.add_goal("目标")
        status = engine.get_status()
        assert status["active_count"] == 1
        assert "goals" in status
