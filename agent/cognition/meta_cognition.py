"""元认知监控（MetaCognitionMonitor）

参考 LAAP（Living Agent Application Protocol）认知架构第 4.5 节。

元认知是对"自身认知过程"的监控与调节——"思考的思考"。
本模块实现四层元认知监控的简化版：

1. 偏差检测：基于行为统计识别 7 种常见认知偏差：
   - confirmation_bias（确认偏差）：反复使用同策略且只关注成功证据
   - overconfidence（过度自信）：预测置信度显著高于实际表现
   - anchoring（锚定效应）：首条信息过度主导后续判断
   - availability（可得性启发）：依赖近期经验而忽略长期统计
   - sunk_cost（沉没成本）：坚持失败策略，不愿止损
   - recency（近因偏差）：只看最近结果
   - overgeneralization（以偏概全）：小样本下过早下结论
2. 思考模式推荐：根据当前认知负载 / 偏差信号，推荐思考模式
   （reflective / deliberate / analytical / creative）。
3. 监控报告：供 Agent 在回复前调整（如过度自信时降低语气）。

与自我模型的衔接：置信度校准数据（self_model）可喂入本模块，
检测 overconfidence 偏差。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from core.logger import get_logger

logger = get_logger(__name__)

# 认知偏差定义
BIAS_NAMES = [
    "confirmation_bias",
    "overconfidence",
    "anchoring",
    "availability",
    "sunk_cost",
    "recency",
    "overgeneralization",
]

# 思考模式
THINKING_MODES = ["reflective", "deliberate", "analytical", "creative"]


@dataclass
class DecisionRecord:
    """一次决策记录（供偏差检测）"""

    domain: str
    strategy: str
    success: bool
    confidence: float | None = None  # 决策前的预测置信度
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "strategy": self.strategy,
            "success": self.success,
            "confidence": self.confidence,
        }


@dataclass
class BiasReport:
    """单个偏差的检测报告"""

    name: str
    active: bool
    score: float
    evidence: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "active": self.active,
            "score": round(self.score, 3),
            "evidence": self.evidence,
        }


class MetaCognitionMonitor:
    """元认知监控器。

    Usage::

        meta = MetaCognitionMonitor()
        meta.track_decision(domain="debugging", strategy="memory_query",
                            success=True, confidence=0.95)
        reports = meta.detect_biases()          # 偏差检测
        mode = meta.recommend_thinking_mode()   # 思考模式推荐
    """

    def __init__(self, history_capacity: int = 200) -> None:
        self._decisions: deque[DecisionRecord] = deque(maxlen=history_capacity)
        self._anchoring_seed: str | None = None
        self._strategy_stats: dict[str, dict[str, float]] = {}

    # ── 决策追踪 ──────────────────────────────────────────────────────────

    def track_decision(
        self,
        domain: str,
        strategy: str,
        success: bool,
        confidence: float | None = None,
    ) -> None:
        """记录一次决策及其结果。

        Args:
            domain: 决策领域。
            strategy: 采用的策略 / 工具名。
            success: 是否成功。
            confidence: 决策前的预测置信度（0-1，用于过自信检测）。
        """
        self._decisions.append(
            DecisionRecord(domain=domain, strategy=strategy, success=success, confidence=confidence)
        )
        # 锚定种子：第一个决策使用的策略
        if self._anchoring_seed is None:
            self._anchoring_seed = f"{domain}:{strategy}"
        # 策略统计
        key = f"{domain}:{strategy}"
        stats = self._strategy_stats.setdefault(key, {"attempts": 0, "successes": 0})
        stats["attempts"] += 1
        if success:
            stats["successes"] += 1

    def track_confidence(self, predicted: float, actual: float) -> None:
        """记录一次置信度校准（可对接自我模型）。"""
        self._decisions.append(
            DecisionRecord(
                domain="calibration",
                strategy="confidence",
                success=actual >= 0.5,
                confidence=predicted,
            )
        )

    # ── 偏差检测 ──────────────────────────────────────────────────────────

    def detect_biases(self) -> list[BiasReport]:
        """检测全部 7 种认知偏差。"""
        reports: list[BiasReport] = []
        reports.append(self._check_confirmation_bias())
        reports.append(self._check_overconfidence())
        reports.append(self._check_anchoring())
        reports.append(self._check_availability())
        reports.append(self._check_sunk_cost())
        reports.append(self._check_recency())
        reports.append(self._check_overgeneralization())
        return reports

    def _check_confirmation_bias(self) -> BiasReport:
        """确认偏差：有失败经验却仍继续使用该策略。"""
        for key, stats in self._strategy_stats.items():
            if stats["attempts"] >= 4 and stats["successes"] / stats["attempts"] < 0.5:
                return BiasReport(
                    name="confirmation_bias",
                    active=True,
                    score=stats["successes"] / stats["attempts"],
                    evidence=f"策略 {key} 成功率偏低但仍在使用",
                )
        return BiasReport(name="confirmation_bias", active=False, score=0.0, evidence="")

    def _check_overconfidence(self) -> BiasReport:
        """过度自信：预测置信度均值显著高于实际成功率。"""
        confidences = [d.confidence for d in self._decisions if d.confidence is not None]
        if len(confidences) < 5:
            return BiasReport(name="overconfidence", active=False, score=0.0, evidence="样本不足")
        predicted_avg = sum(confidences) / len(confidences)
        success_rate = sum(1 for d in self._decisions if d.success) / len(self._decisions)
        bias = predicted_avg - success_rate
        return BiasReport(
            name="overconfidence",
            active=bias > 0.2,
            score=bias,
            evidence=f"预测置信度 {predicted_avg:.2f} vs 实际成功率 {success_rate:.2f}",
        )

    def _check_anchoring(self) -> BiasReport:
        """锚定效应：首策略使用次数占比过高。"""
        if not self._anchoring_seed or not self._decisions:
            return BiasReport(name="anchoring", active=False, score=0.0, evidence="")
        anchor_uses = self._strategy_stats.get(self._anchoring_seed, {}).get("attempts", 0)
        total = len(self._decisions)
        ratio = anchor_uses / total
        return BiasReport(
            name="anchoring",
            active=ratio > 0.7,
            score=ratio,
            evidence=f"首个策略 {self._anchoring_seed} 使用占比 {ratio:.0%}",
        )

    def _check_availability(self) -> BiasReport:
        """可得性启发：决策高度集中于最近一个策略。"""
        if not self._decisions:
            return BiasReport(name="availability", active=False, score=0.0, evidence="")
        recent = list(self._decisions)[-10:]
        if len(recent) < 5:
            return BiasReport(name="availability", active=False, score=0.0, evidence="样本不足")
        last_strategy = recent[-1].strategy
        recent_uses = sum(1 for d in recent if d.strategy == last_strategy)
        ratio = recent_uses / len(recent)
        return BiasReport(
            name="availability",
            active=ratio > 0.6,
            score=ratio,
            evidence=f"最近 10 次决策中 {ratio:.0%} 使用同一策略 {last_strategy}",
        )

    def _check_sunk_cost(self) -> BiasReport:
        """沉没成本：策略连续失败 ≥3 次仍不切换。"""
        if len(self._decisions) < 3:
            return BiasReport(name="sunk_cost", active=False, score=0.0, evidence="样本不足")
        recent = list(self._decisions)[-10:]
        last_key = f"{recent[-1].domain}:{recent[-1].strategy}"
        consecutive_fails = 0
        for d in reversed(recent):
            if f"{d.domain}:{d.strategy}" == last_key:
                if not d.success:
                    consecutive_fails += 1
                else:
                    break
            else:
                break
        return BiasReport(
            name="sunk_cost",
            active=consecutive_fails >= 3,
            score=consecutive_fails,
            evidence=f"策略 {last_key} 连续失败 {consecutive_fails} 次",
        )

    def _check_recency(self) -> BiasReport:
        """近因偏差：只依据最近 3 次结果而非长期统计。

        仅统计非校准类决策（calibration 记录不代表真实任务，避免污染成功率）。
        """
        real = [d for d in self._decisions if d.domain != "calibration"]
        if len(real) < 10:
            return BiasReport(name="recency", active=False, score=0.0, evidence="样本不足")
        recent = real[-3:]
        overall_rate = sum(1 for d in real if d.success) / len(real)
        recent_rate = sum(1 for d in recent if d.success) / len(recent)
        gap = recent_rate - overall_rate
        return BiasReport(
            name="recency",
            active=abs(gap) > 0.4,
            score=gap,
            evidence=f"近期成功率 {recent_rate:.2f} vs 总体 {overall_rate:.2f}",
        )

    def _check_overgeneralization(self) -> BiasReport:
        """以偏概全：样本极少（<4）却形成了高置信的策略。"""
        for key, stats in self._strategy_stats.items():
            if 0 < stats["attempts"] < 4 and stats["successes"] == stats["attempts"]:
                return BiasReport(
                    name="overgeneralization",
                    active=True,
                    score=stats["attempts"],
                    evidence=f"策略 {key} 仅 {stats['attempts']} 次尝试即视为可靠",
                )
        return BiasReport(name="overgeneralization", active=False, score=0.0, evidence="")

    # ── 思考模式推荐 ──────────────────────────────────────────────────────

    def recommend_thinking_mode(self) -> str:
        """根据当前偏差状态推荐思考模式。

        Returns:
            reflective / deliberate / analytical / creative
        """
        reports = self.detect_biases()
        active = [r for r in reports if r.active]
        if any(r.name in ("overconfidence", "sunk_cost") for r in active):
            return "reflective"      # 需要复盘
        if any(r.name in ("confirmation_bias", "availability", "anchoring") for r in active):
            return "deliberate"      # 需要刻意多样化
        if any(r.name == "overgeneralization" for r in active):
            return "analytical"      # 需要更多证据
        if len(self._decisions) >= 30 and len(self._strategy_stats) >= 3:
            return "creative"        # 经验充足可创新
        return "deliberate"

    # ── 状态报告 ──────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "decisions_tracked": len(self._decisions),
            "strategies": {
                k: {"attempts": int(v["attempts"]), "success_rate": round(v["successes"] / v["attempts"], 3) if v["attempts"] else 0}
                for k, v in self._strategy_stats.items()
            },
            "active_biases": [r.to_dict() for r in self.detect_biases() if r.active],
            "recommended_mode": self.recommend_thinking_mode(),
        }


__all__ = ["DecisionRecord", "BiasReport", "MetaCognitionMonitor", "BIAS_NAMES", "THINKING_MODES"]
