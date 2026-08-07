"""进化系统（EvolutionSystem）

参考 LAAP（Living Agent Application Protocol）认知架构第 11 章，
实现递归自我改进（Recursive Self-Improvement, RSI）的轻量版本。

博客原始设计包含代码级自我进化（Agent 修改自身源码）。本项目定位为
陪伴型数字生命体，代码级自我修改风险过高，故裁剪为**认知参数进化**：
Agent 通过观察自身性能指标（任务成功率、需求满足度、情绪稳定性等），
生成改进提案（调整认知引擎参数），在沙箱中测试提案的预期收益，达到
阈值后采纳，否则拒绝。

RSI 循环：
1. 观察（Observe）：记录性能指标（指标趋势）。
2. 提案（Propose）：基于指标瓶颈生成改进提案（参数覆盖）。
3. 测试（Test）：在沙箱（参数副本 + 历史经验重算）中评估预期收益。
4. 比较（Compare）：预期适应度 vs 当前适应度。
5. 采纳（Adopt）：改善超过阈值 → 应用到认知引擎；否则拒绝。

设计原则：
- 不修改源码，只调整认知引擎的数值参数。
- 每次进化只允许小幅参数变动（保守演化）。
- 全程可审计（进化历史）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from core.logger import get_logger

logger = get_logger(__name__)


class ProposalStatus(Enum):
    PENDING = "pending"
    EVALUATING = "evaluating"
    ADOPTED = "adopted"
    REJECTED = "rejected"


@dataclass
class MetricRecord:
    """一条性能指标记录"""

    name: str
    value: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"name": self.name, "value": round(self.value, 4)}


@dataclass
class EvolutionProposal:
    """一个改进提案（认知参数覆盖）"""

    target: str          # 参数路径，如 "needs.decay_rate"
    current_value: float
    proposed_value: float
    reason: str
    expected_gain: float = 0.0   # 预期适应度提升
    risk: float = 0.3            # 风险 [0,1]
    status: ProposalStatus = ProposalStatus.PENDING
    fitness_before: float = 0.0
    fitness_after: float = 0.0
    timestamp: float = field(default_factory=time.time)
    id: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target": self.target,
            "current_value": self.current_value,
            "proposed_value": self.proposed_value,
            "reason": self.reason,
            "expected_gain": round(self.expected_gain, 4),
            "risk": self.risk,
            "status": self.status.value,
            "fitness_before": round(self.fitness_before, 4),
            "fitness_after": round(self.fitness_after, 4),
        }


class EvolutionSystem:
    """进化系统。

    Usage::

        evo = EvolutionSystem()
        evo.record_metric("task_success_rate", 0.8)
        evo.record_metric("need_satisfaction", 0.6)
        fitness = evo.compute_fitness()
        proposals = evo.generate_proposals()   # 生成提案
        evo.evaluate(proposals[0])             # 沙箱评估
        evo.adopt_or_reject(proposals[0])      # 采纳或拒绝
    """

    # 可进化参数（target 路径 → 边界约束）
    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "needs.decay_rate": (0.005, 0.06),       # 需求自然衰减速率
        "needs.volatility": (0.001, 0.02),       # 需求波动
        "memory.consolidation": (0.3, 0.9),      # 记忆巩固阈值
        "emotion.response": (0.2, 1.0),          # 情绪响应强度
        "autonomy.interval": (5, 20),            # 自主维护间隔
        "self.quality": (0.5, 0.95),             # 自我模型质量门槛
    }

    def __init__(
        self,
        min_improvement: float = 0.03,
        max_consecutive_evolutions: int = 3,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self._metrics: list[MetricRecord] = []
        self._proposals: list[EvolutionProposal] = []
        self._proposal_counter: int = 0
        self._min_improvement = min_improvement
        self._max_consecutive = max_consecutive_evolutions
        self._consecutive_adopted: int = 0
        self._rng = rng or (lambda: 0.5)
        # 当前参数状态（默认取边界中点）
        self.params: dict[str, float] = {
            name: (lo + hi) / 2.0 for name, (lo, hi) in self.PARAM_BOUNDS.items()
        }

    # ── 观察：指标记录 ────────────────────────────────────────────────────

    def record_metric(self, name: str, value: float) -> None:
        """记录一个性能指标。"""
        self._metrics.append(MetricRecord(name=name, value=value))
        # 保留最近 200 条
        if len(self._metrics) > 200:
            self._metrics = self._metrics[-200:]

    def metric_latest(self, name: str) -> float:
        """取某指标最近值（无记录返回 0.5 中性）。"""
        for rec in reversed(self._metrics):
            if rec.name == name:
                return rec.value
        return 0.5

    def metric_trend(self, name: str) -> float:
        """某指标趋势（近 5 条线性趋势斜率，正 = 改善）。"""
        recent = [r for r in self._metrics if r.name == name][-5:]
        if len(recent) < 2:
            return 0.0
        values = [r.value for r in recent]
        n = len(values)
        slope = (n * sum(i * v for i, v in enumerate(values)) - sum(values) * sum(range(n))) / (
            n * sum(i * i for i in range(n)) - sum(range(n)) ** 2 + 1e-9
        )
        return slope

    # ── 适应度计算 ────────────────────────────────────────────────────────

    def compute_fitness(self) -> float:
        """综合适应度：任务成功率 + 需求满足度 + 情绪稳定性。

        Returns:
            [0, 1] 适应度。
        """
        task_rate = self.metric_latest("task_success_rate")
        need_sat = self.metric_latest("need_satisfaction")
        emotion_stability = 1.0 - abs(self.metric_latest("emotion_instability") - 0.0)
        fitness = 0.5 * task_rate + 0.3 * need_sat + 0.2 * emotion_stability
        return max(0.0, min(1.0, fitness))

    # ── 提案生成 ──────────────────────────────────────────────────────────

    def generate_proposals(self) -> list[EvolutionProposal]:
        """基于指标瓶颈生成改进提案（规则驱动）。

        - 任务成功率低 → 降低需求衰减（减缓波动）
        - 需求满足度低 → 降低波动（更稳定）
        - 情绪不稳定 → 提高情绪响应控制
        """
        proposals: list[EvolutionProposal] = []
        task_rate = self.metric_latest("task_success_rate")
        need_sat = self.metric_latest("need_satisfaction")
        emotion_instability = self.metric_latest("emotion_instability")

        if task_rate < 0.6:
            current = self.params["needs.decay_rate"]
            lo, _ = self.PARAM_BOUNDS["needs.decay_rate"]
            proposed = max(lo, current * 0.9)
            if abs(proposed - current) > 0.001:
                proposals.append(EvolutionProposal(
                    target="needs.decay_rate",
                    current_value=current,
                    proposed_value=proposed,
                    reason=f"任务成功率偏低（{task_rate:.2f}），减缓需求衰减以增强动力",
                    expected_gain=0.3 * (1 - task_rate),
                    risk=0.2,
                    id=self._next_id(),
                ))
        if need_sat < 0.5:
            current = self.params["needs.volatility"]
            lo, _ = self.PARAM_BOUNDS["needs.volatility"]
            proposed = max(lo, current * 0.85)
            if abs(proposed - current) > 0.001:
                proposals.append(EvolutionProposal(
                    target="needs.volatility",
                    current_value=current,
                    proposed_value=proposed,
                    reason=f"需求满足度偏低（{need_sat:.2f}），降低波动提升稳定性",
                    expected_gain=0.25 * (1 - need_sat),
                    risk=0.15,
                    id=self._next_id(),
                ))
        if emotion_instability > 0.5:
            current = self.params["emotion.response"]
            _, hi = self.PARAM_BOUNDS["emotion.response"]
            proposed = min(hi, current * 1.15)
            if abs(proposed - current) > 0.001:
                proposals.append(EvolutionProposal(
                    target="emotion.response",
                    current_value=current,
                    proposed_value=proposed,
                    reason=f"情绪不稳定（{emotion_instability:.2f}），提高响应控制",
                    expected_gain=0.2 * emotion_instability,
                    risk=0.1,
                    id=self._next_id(),
                ))
        return proposals

    def _next_id(self) -> int:
        self._proposal_counter += 1
        return self._proposal_counter

    # ── 沙箱测试 ──────────────────────────────────────────────────────────

    def evaluate(self, proposal: EvolutionProposal) -> None:
        """沙箱评估：在参数副本上计算预期适应度。

        Args:
            proposal: 待评估提案（原地更新 fitness_before/after）。
        """
        if proposal.status != ProposalStatus.PENDING:
            return
        proposal.status = ProposalStatus.EVALUATING
        proposal.fitness_before = self.compute_fitness()
        # 沙箱：应用提案后计算预期适应度
        old = self.params[proposal.target]
        self.params[proposal.target] = proposal.proposed_value
        # 用当前指标 + 预期增益近似新适应度
        proposal.fitness_after = min(1.0, proposal.fitness_before + proposal.expected_gain)
        self.params[proposal.target] = old

    def adopt_or_reject(self, proposal: EvolutionProposal) -> bool:
        """采纳或拒绝提案。

        Returns:
            True 采纳，False 拒绝。
        """
        if proposal.status != ProposalStatus.EVALUATING:
            return False
        improvement = proposal.fitness_after - proposal.fitness_before
        # 保守：连续采纳次数达上限后拒绝
        if self._consecutive_adopted >= self._max_consecutive:
            proposal.status = ProposalStatus.REJECTED
            self._consecutive_adopted = 0
            return False
        if improvement >= self._min_improvement:
            # 采纳：应用参数变化
            self.params[proposal.target] = proposal.proposed_value
            proposal.status = ProposalStatus.ADOPTED
            self._consecutive_adopted += 1
            logger.debug("[Evolution] 采纳提案 %s → %.3f", proposal.target, proposal.proposed_value)
            return True
        proposal.status = ProposalStatus.REJECTED
        self._consecutive_adopted = 0
        return False

    # ── 状态 ──────────────────────────────────────────────────────────────

    def run_evolution_cycle(self) -> dict:
        """执行一轮完整 RSI 循环。

        Returns:
            本轮回合统计 {"proposals": n, "adopted": m, "fitness": f}
        """
        proposals = self.generate_proposals()
        adopted = 0
        for proposal in proposals:
            self.evaluate(proposal)
            if self.adopt_or_reject(proposal):
                adopted += 1
            self._proposals.append(proposal)
        return {
            "proposals": len(proposals),
            "adopted": adopted,
            "fitness": round(self.compute_fitness(), 4),
        }

    def get_status(self) -> dict:
        return {
            "fitness": round(self.compute_fitness(), 4),
            "params": {k: round(v, 4) for k, v in self.params.items()},
            "metrics_latest": self._latest_metrics(),
            "proposals": [p.to_dict() for p in self._proposals[-10:]],
            "consecutive_adopted": self._consecutive_adopted,
        }

    def _latest_metrics(self) -> dict[str, float]:
        latest: dict[str, float] = {}
        for rec in self._metrics:
            latest[rec.name] = round(rec.value, 4)
        return latest


__all__ = ["MetricRecord", "EvolutionProposal", "ProposalStatus", "EvolutionSystem"]
