"""测试规划器（planner.py）"""

# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from agent.cognition.planner import Planner, StepStatus


class TestPlanner:
    def test_create_plan(self):
        planner = Planner()
        pid = planner.create_plan("学会安慰用户", "comfort")
        plan = planner.get_plan(pid)
        assert plan is not None
        assert plan.plan_type == "comfort"
        assert len(plan.steps) == 3
        assert plan.current_step().action == "认真倾听用户表达"

    def test_create_plan_unknown_type_falls_back(self):
        planner = Planner()
        pid = planner.create_plan("未知", "unknown_type")
        plan = planner.get_plan(pid)
        assert plan.plan_type == "learn"  # 回退到默认模板

    def test_advance_to_next_step(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        planner.advance(pid, success=True)
        plan = planner.get_plan(pid)
        assert plan.steps[0].status == StepStatus.DONE
        assert plan.current_step().action == "尝试一个具体行动"

    def test_advance_failure_triggers_failed(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        for _ in range(3):  # 超过单步最大尝试次数
            planner.advance(pid, success=False)
        plan = planner.get_plan(pid)
        assert plan.steps[0].status == StepStatus.FAILED

    def test_is_stalled(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        for _ in range(3):
            planner.advance(pid, success=False)
        assert planner.is_stalled(pid) is True

    def test_replan_resets_steps(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        for _ in range(3):
            planner.advance(pid, success=False)
        assert planner.is_stalled(pid) is True
        planner.replan(pid, reason="停滞")
        assert planner.is_stalled(pid) is False
        plan = planner.get_plan(pid)
        assert plan.steps[0].status == StepStatus.PENDING
        assert planner._replan_count == 1

    def test_is_complete(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        assert planner.is_complete(pid) is False
        for _ in range(3):
            planner.advance(pid, success=True)
        assert planner.is_complete(pid) is True

    def test_progress(self):
        planner = Planner()
        pid = planner.create_plan("学习", "learn")
        planner.advance(pid, success=True)
        assert planner.get_plan(pid).progress() == pytest.approx(1 / 3)

    def test_get_status(self):
        planner = Planner()
        planner.create_plan("学习", "learn")
        status = planner.get_status()
        assert status["active"] == 1
        assert status["replan_count"] == 0
        assert len(status["plans"]) == 1
