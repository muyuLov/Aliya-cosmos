"""涌现自我模型（EmergentSelfModel）

参考 LAAP（Living Agent Application Protocol）认知架构第 5.3 节。

核心思想：自我认知不是静态注入的系统提示词，而是从经验中涌现的。

三大机制：
1. 能力档案（SkillProfile）：按领域统计尝试 / 成功 / 质量 / 增长率，
   熟练度按证据自动演进：UNEXPLORED → BEGINNER → DEVELOPING →
   COMPETENT → EXPERT → MASTER。
2. 置信度校准：记录每次行动前的预测置信度与实际结果，检测过度自信
   / 信心不足偏差。
3. 自传体叙事：重大事件（首次达标、意外失败、突破）被记录为自传体
   事件，构成 Agent 的"个人历史"。

接口：
- record_experience(domain, outcome, ...)：核心学习循环
- know_what_you_know()：自我知识报告
- self_assess(domain, required)：证据驱动的任务就绪判断
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from core.logger import get_logger

logger = get_logger(__name__)


# 熟练度等级（升序）
PROFICIENCY_LEVELS = [
    "unexplored",
    "beginner",
    "developing",
    "competent",
    "expert",
    "master",
]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class SkillProfile:
    """领域技能档案（全部从经验中学习）"""

    domain: str
    attempts: int = 0
    successes: int = 0
    quality_sum: float = 0.0
    recent: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    last_updated: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def avg_quality(self) -> float:
        return self.quality_sum / self.attempts if self.attempts else 0.0

    @property
    def growth_rate(self) -> float:
        """增长率：近 20 次结果均值的波动率。"""
        if len(self.recent) < 5:
            return 0.0
        recent_avg = sum(self.recent) / len(self.recent)
        return recent_avg - self.success_rate

    @property
    def proficiency(self) -> str:
        """熟练度等级：基于成功率与质量综合评分。"""
        if self.attempts == 0:
            return "unexplored"
        score = self.success_rate * 0.7 + self.avg_quality * 0.3
        if score >= 0.9:
            return "master"
        if score >= 0.75:
            return "expert"
        if score >= 0.55:
            return "competent"
        if score >= 0.35:
            return "developing"
        return "beginner"

    def record(self, success: bool, quality: float = 0.5) -> None:
        """记录一次行动结果。"""
        self.attempts += 1
        if success:
            self.successes += 1
        self.quality_sum += quality
        self.recent.append(1.0 if success else quality)
        self.last_updated = time.time()

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "attempts": self.attempts,
            "successes": self.successes,
            "success_rate": round(self.success_rate, 3),
            "avg_quality": round(self.avg_quality, 3),
            "growth_rate": round(self.growth_rate, 3),
            "proficiency": self.proficiency,
        }


@dataclass
class ConfidenceRecord:
    """置信度校准记录"""

    predicted: float
    actual: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class AutobiographicalEvent:
    """自传体事件（个人历史）"""

    description: str
    significance: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "significance": round(self.significance, 3),
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.timestamp)),
        }


class EmergentSelfModel:
    """涌现自我模型。

    Usage::

        self_model = EmergentSelfModel()
        self_model.record_experience("python_debugging", success=True, quality=0.9)
        report = self_model.know_what_you_know()
        assessment = self_model.self_assess("python_debugging")
    """

    def __init__(self, confidence_window: int = 1000) -> None:
        self.skills: dict[str, SkillProfile] = {}
        self.confidence_history: deque[ConfidenceRecord] = deque(maxlen=confidence_window)
        self.autobiography: list[AutobiographicalEvent] = []
        self.total_actions: int = 0

    # ── 核心学习循环 ──────────────────────────────────────────────────────

    def record_experience(
        self,
        domain: str,
        *,
        success: bool = True,
        quality: float = 0.5,
        predicted_confidence: float | None = None,
        was_surprising: bool = False,
        description: str = "",
    ) -> SkillProfile:
        """记录一次领域经验。

        Args:
            domain: 领域名称（如 "python_debugging"、"tools/memory_query"）。
            success: 是否成功。
            quality: 结果质量分数 [0, 1]。
            predicted_confidence: 行动前的预测置信度（用于校准）。
            was_surprising: 是否为意外结果（促成自传体记录）。
            description: 事件描述（自传体叙事用）。

        Returns:
            更新后的 SkillProfile。
        """
        skill = self.skills.setdefault(domain, SkillProfile(domain=domain))
        skill.record(success, quality)
        self.total_actions += 1

        if predicted_confidence is not None:
            self.confidence_history.append(
                ConfidenceRecord(predicted=predicted_confidence, actual=1.0 if success else quality)
            )

        # 自传体事件：重大事件（首次达标 / 意外失败 / 突破性成功）
        significance = quality
        if was_surprising:
            significance = max(significance, 0.7)
        if skill.attempts == 1 and success:
            significance = max(significance, 0.6)
        if significance >= 0.6:
            self.autobiography.append(
                AutobiographicalEvent(
                    description=description or f"{domain} 经验（{'成功' if success else '失败'}）",
                    significance=significance,
                )
            )
        return skill

    # ── 自我认知查询 ──────────────────────────────────────────────────────

    def know_what_you_know(self) -> dict:
        """自我知识报告：最强 / 最弱 / 未探索领域 + 校准状态。"""
        if not self.skills:
            return {"status": "unexplored", "skills_tracked": 0}

        by_level: dict[str, list[str]] = {level: [] for level in PROFICIENCY_LEVELS}
        for domain, skill in self.skills.items():
            by_level[skill.proficiency].append(domain)

        return {
            "status": "learning",
            "skills_tracked": len(self.skills),
            "total_actions": self.total_actions,
            "strong_domains": by_level["expert"] + by_level["master"],
            "competent_domains": by_level["competent"],
            "developing_domains": by_level["developing"] + by_level["beginner"],
            "unexplored": by_level["unexplored"],
            "calibration": self.get_calibration(),
        }

    def self_assess(self, domain: str, required: str = "competent") -> dict:
        """证据驱动的任务就绪判断。

        Args:
            domain: 待评估领域。
            required: 所需最低熟练度等级。

        Returns:
            {"ready": bool, "confidence": str, "proficiency": str, "advice": str}
        """
        if required not in PROFICIENCY_LEVELS:
            required = "competent"
        skill = self.skills.get(domain)
        if skill is None or skill.attempts == 0:
            return {
                "ready": False,
                "confidence": "none",
                "proficiency": "unexplored",
                "advice": "尚无该领域经验，谨慎行事",
            }
        required_idx = PROFICIENCY_LEVELS.index(required)
        current_idx = PROFICIENCY_LEVELS.index(skill.proficiency)
        if current_idx >= required_idx:
            return {
                "ready": True,
                "confidence": "high" if current_idx >= required_idx + 1 else "moderate",
                "proficiency": skill.proficiency,
                "advice": f"该领域熟练度为 {skill.proficiency}，可以胜任",
            }
        return {
            "ready": False,
            "confidence": "low",
            "proficiency": skill.proficiency,
            "advice": f"该领域熟练度不足（{skill.proficiency}），建议谨慎或先学习",
        }

    def get_calibration(self) -> dict:
        """置信度校准：对比预测置信度与实际结果均值。"""
        if not self.confidence_history:
            return {"samples": 0, "bias": 0.0, "status": "insufficient_data"}
        records = list(self.confidence_history)
        predicted_avg = sum(r.predicted for r in records) / len(records)
        actual_avg = sum(r.actual for r in records) / len(records)
        bias = predicted_avg - actual_avg  # >0 过度自信
        status = "calibrated"
        if bias > 0.15:
            status = "overconfident"
        elif bias < -0.15:
            status = "underconfident"
        return {
            "samples": len(records),
            "predicted_avg": round(predicted_avg, 3),
            "actual_avg": round(actual_avg, 3),
            "bias": round(bias, 3),
            "status": status,
        }

    # ── 摘要与统计 ────────────────────────────────────────────────────────

    def to_summary(self, limit: int = 5) -> str:
        """生成 LLM 可读的自我模型摘要。"""
        if not self.skills:
            return "自我认知尚未形成（缺少经验）。"
        lines: list[str] = []
        top = sorted(
            self.skills.values(), key=lambda s: s.attempts, reverse=True
        )[:limit]
        for skill in top:
            lines.append(
                f"- {skill.domain}: {skill.proficiency}（尝试 {skill.attempts} 次，"
                f"成功率 {skill.success_rate:.0%}）"
            )
        calibration = self.get_calibration()
        if calibration.get("samples", 0) >= 5:
            lines.append(f"- 置信度校准: {calibration['status']}（偏差 {calibration['bias']:+.2f}）")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "skills_tracked": len(self.skills),
            "total_actions": self.total_actions,
            "autobiographical_events": len(self.autobiography),
            "calibration_samples": len(self.confidence_history),
        }


__all__ = [
    "SkillProfile",
    "ConfidenceRecord",
    "AutobiographicalEvent",
    "EmergentSelfModel",
    "PROFICIENCY_LEVELS",
]
