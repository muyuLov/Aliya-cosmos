"""测试涌现自我模型（self_model.py）"""

from __future__ import annotations

import pytest

from agent.cognition.self_model import (
    EmergentSelfModel,
    SkillProfile,
)


class TestSkillProfile:
    def test_default_proficiency_unexplored(self):
        profile = SkillProfile(domain="test")
        assert profile.proficiency == "unexplored"
        assert profile.attempts == 0

    def test_record_updates_stats(self):
        profile = SkillProfile(domain="debugging")
        profile.record(success=True, quality=0.9)
        profile.record(success=True, quality=0.8)
        assert profile.attempts == 2
        assert profile.successes == 2
        assert profile.success_rate == pytest.approx(1.0)

    def test_proficiency_progression(self):
        profile = SkillProfile(domain="debugging")
        for _ in range(20):
            profile.record(success=True, quality=0.95)
        assert profile.proficiency in ("expert", "master")

    def test_failures_lower_proficiency(self):
        profile = SkillProfile(domain="debugging")
        for _ in range(10):
            profile.record(success=False, quality=0.1)
        assert profile.proficiency == "beginner"


class TestEmergentSelfModel:
    def test_record_experience_creates_skill(self):
        sm = EmergentSelfModel()
        profile = sm.record_experience("python_debugging", success=True, quality=0.9)
        assert sm.skills["python_debugging"] is profile
        assert sm.total_actions == 1

    def test_know_what_you_know_empty(self):
        sm = EmergentSelfModel()
        report = sm.know_what_you_know()
        assert report["status"] == "unexplored"
        assert report["skills_tracked"] == 0

    def test_know_what_you_know_with_skills(self):
        sm = EmergentSelfModel()
        for _ in range(10):
            sm.record_experience("python_debugging", success=True, quality=0.9)
        report = sm.know_what_you_know()
        assert report["skills_tracked"] == 1
        assert "python_debugging" in report["strong_domains"]

    def test_self_assess_ready(self):
        sm = EmergentSelfModel()
        for _ in range(10):
            sm.record_experience("python_debugging", success=True, quality=0.9)
        assessment = sm.self_assess("python_debugging", required="competent")
        assert assessment["ready"] is True

    def test_self_assess_unexplored(self):
        sm = EmergentSelfModel()
        assessment = sm.self_assess("unknown_domain")
        assert assessment["ready"] is False
        assert assessment["proficiency"] == "unexplored"

    def test_self_assess_insufficient(self):
        sm = EmergentSelfModel()
        sm.record_experience("python_debugging", success=False, quality=0.1)
        assessment = sm.self_assess("python_debugging", required="competent")
        assert assessment["ready"] is False

    def test_calibration_detects_overconfidence(self):
        sm = EmergentSelfModel()
        # 预测 0.95 但实际均失败（actual=quality=0.1）→ 过度自信
        for _ in range(10):
            sm.record_experience(
                "domain", success=False, quality=0.1, predicted_confidence=0.95
            )
        calibration = sm.get_calibration()
        assert calibration["status"] == "overconfident"

    def test_calibration_insufficient_data(self):
        sm = EmergentSelfModel()
        calibration = sm.get_calibration()
        assert calibration["status"] == "insufficient_data"

    def test_autobiography_on_surprising(self):
        sm = EmergentSelfModel()
        sm.record_experience(
            "domain", success=False, quality=0.1, was_surprising=True
        )
        assert len(sm.autobiography) == 1

    def test_autobiography_on_first_success(self):
        sm = EmergentSelfModel()
        sm.record_experience("domain", success=True, quality=0.8)
        assert len(sm.autobiography) == 1

    def test_to_summary(self):
        sm = EmergentSelfModel()
        for _ in range(5):
            sm.record_experience("python_debugging", success=True, quality=0.9)
        summary = sm.to_summary()
        assert "python_debugging" in summary

    def test_get_stats(self):
        sm = EmergentSelfModel()
        stats = sm.get_stats()
        assert stats["skills_tracked"] == 0
        assert stats["total_actions"] == 0
