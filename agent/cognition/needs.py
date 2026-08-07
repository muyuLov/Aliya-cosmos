"""PSI 需求驱动系统（NeedDriveSystem）

参考 LAAP（Living Agent Application Protocol）认知架构第 4 章，实现
Dörner PSI 理论的需求驱动内在动机系统。

核心思想：Agent 的行为由"需求满足"而非"指令执行"驱动。
行为选择函数：
    action = argmax(α · ExternalUtility(action | goal) +
                    (1-α) · InternalDrive(action | needs))
其中 α 为动态权重，由当前需求赤字程度决定。

五大基本需求：
- CERTAINTY（确定性）：对环境的预测能力。工具失败、意外结果 → 确定性下降，驱使探索。
- COMPETENCE（胜任感）：对自己能力的满足。成功调用、正确回答 → 胜任感提升。
- AUTONOMY（自主性）：行为选择自由度。长期被动响应 → 自主性赤字累积。
- RELATEDNESS（归属感）：与用户的社交连接质量。影响沟通风格。
- ENERGY（能量）：计算资源状态。token 消耗大 → 能量赤字，促使节约。

需求动态（decay-satisfaction）：
- tick：自然衰减 + 随机波动
- satisfy：外部事件满足需求
- deficit = max(0, target - current) → 需求赤字，驱动力来源

需求驱动情绪梯度（第 4.3 节，情绪 = 需求满足的微分信号）：
- valence = clip(2 · mean(satisfactions) - 1, -1, 1)
- arousal = 0.3 + 0.7 · |mean(Δsatisfactions)|
- dominance = 0.2 + 0.8 · task_success_rate

该梯度可直接推入现有 VAD 情绪状态机（EmotionStateController）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from core.logger import get_logger

logger = get_logger(__name__)


class NeedType(Enum):
    """PSI 五大基本需求"""

    CERTAINTY = "certainty"
    COMPETENCE = "competence"
    AUTONOMY = "autonomy"
    RELATEDNESS = "relatedness"
    ENERGY = "energy"


@dataclass
class Need:
    """单个需求的动力学状态。

    Attributes:
        name: 需求类型。
        current_level: 当前满足水平 [0, 1]。
        target_level: 目标满足水平 [0, 1]（满足的参照系）。
        decay_rate: 自然衰减速率（每 tick）。
        volatility: 随机波动幅度（每 tick）。
    """

    name: NeedType
    current_level: float = 0.6
    target_level: float = 0.8
    decay_rate: float = 0.02
    volatility: float = 0.005

    def tick(self, dt: float = 1.0, rng: Callable[[], float] | None = None) -> float:
        """按时间步推进：自然衰减 + 随机波动。

        Args:
            dt: 时间步长（秒）。
            rng: 随机数源（[0,1)），用于测试注入确定性随机源。

        Returns:
            推进后的 current_level。
        """
        noise = ((rng() if rng else 0.0) - 0.5) * self.volatility * dt * 2
        self.current_level = _clamp(self.current_level - self.decay_rate * dt + noise, 0.0, 1.0)
        return self.current_level

    def satisfy(self, amount: float) -> float:
        """满足需求：增加当前水平（不高于 1.0）。

        Returns:
            满足后的 current_level。
        """
        self.current_level = min(1.0, self.current_level + amount)
        return self.current_level

    @property
    def deficit(self) -> float:
        """需求赤字：驱动行为的核心信号。"""
        return max(0.0, self.target_level - self.current_level)

    @property
    def satisfaction(self) -> float:
        """满足度（0-1）：current / target 的归一化。"""
        if self.target_level <= 0:
            return 1.0
        return _clamp(self.current_level / self.target_level, 0.0, 1.0)

    def to_dict(self) -> dict:
        return {
            "current": round(self.current_level, 4),
            "target": self.target_level,
            "deficit": round(self.deficit, 4),
            "satisfaction": round(self.satisfaction, 4),
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class NeedDriveSystem:
    """五大需求管理器。

    Usage::

        nds = NeedDriveSystem()
        nds.record_tool_result(success=True)   # 工具调用结果 → 更新需求
        nds.record_user_interaction()          # 用户交互 → 归属感满足
        nds.tick(1.0)                          # 时间推进 → 自然衰减
        gradient = nds.compute_emotion_gradient()  # 需求驱动情绪梯度
    """

    def __init__(
        self,
        rng: Callable[[], float] | None = None,
        initial_levels: dict[NeedType, float] | None = None,
    ) -> None:
        """初始化五大需求。

        Args:
            rng: 随机数源（[0,1)），用于 tick 波动。测试可注入确定性源。
            initial_levels: 初始需求水平覆盖（缺省使用默认值）。
        """
        self._rng = rng or (lambda: 0.0)
        self.needs: dict[NeedType, Need] = {}
        for need_type in NeedType:
            level = (initial_levels or {}).get(need_type, 0.6)
            self.needs[need_type] = Need(name=need_type, current_level=level)

        # 情绪梯度内部状态
        self._prev_satisfactions: dict[NeedType, float] | None = None
        self._task_success: int = 0
        self._task_fail: int = 0
        self._interaction_count: int = 0
        self._token_consumed: int = 0

    # ── 需求操作 ──────────────────────────────────────────────────────────

    def get(self, need_type: NeedType) -> Need:
        return self.needs[need_type]

    def satisfy(self, need_type: NeedType, amount: float) -> None:
        """直接满足某个需求（amount 可为负表示消耗）。"""
        self.needs[need_type].satisfy(amount)

    def tick(self, dt: float = 1.0) -> None:
        """推进所有需求的自然衰减。"""
        for need in self.needs.values():
            need.tick(dt, rng=self._rng)

    # ── 事件驱动更新 ──────────────────────────────────────────────────────

    def record_tool_result(self, success: bool) -> None:
        """记录一次工具调用结果。

        - 成功：胜任感提升、确定性提升（环境可预测）。
        - 失败：确定性下降、胜任感微降。
        """
        if success:
            self._task_success += 1
            self.satisfy(NeedType.COMPETENCE, 0.06)
            self.satisfy(NeedType.CERTAINTY, 0.04)
        else:
            self._task_fail += 1
            self.satisfy(NeedType.CERTAINTY, -0.05)
            self.satisfy(NeedType.COMPETENCE, -0.02)

    def record_user_interaction(self, positive: bool = True) -> None:
        """记录一次用户交互（影响归属感与自主性）。

        - 积极交互：归属感提升。
        - 用户发起请求：自主性消耗（被动响应）。
        """
        self._interaction_count += 1
        self.satisfy(NeedType.RELATEDNESS, 0.05 if positive else 0.02)
        # 用户主动请求使自主性（Agent 行为自由度）赤字累积
        self.satisfy(NeedType.AUTONOMY, -0.02)

    def record_energy_consumption(self, tokens: int) -> None:
        """记录 token 消耗（能量需求）。"""
        self._token_consumed += tokens
        # token 消耗 → 能量满足度下降（按 1000 token 消耗 0.02 计）
        self.satisfy(NeedType.ENERGY, -0.02 * (tokens / 1000.0))

    def record_autonomy_action(self) -> None:
        """记录一次自主行动（Agent 主动发起，非用户指令）。

        自主行动满足自主性需求。
        """
        self.satisfy(NeedType.AUTONOMY, 0.06)

    # ── 需求驱动向量 ──────────────────────────────────────────────────────

    def compute_drive_vector(self) -> dict[str, float]:
        """返回各需求的赤字（驱动力）向量。"""
        return {nt.value: need.deficit for nt, need in self.needs.items()}

    def compute_dynamic_alpha(self) -> float:
        """计算外部效用权重 α。

        当需求赤字总和高时 α 减小，Agent 更倾向满足内在需求；
        需求满足度高时 α 增大，更倾向执行外部任务。
        """
        total_deficit = sum(need.deficit for need in self.needs.values())
        # 赤字范围 [0, ~2]（5 需求各 max 1.0 但 target 一般 < 1）
        return _clamp(1.0 - total_deficit / 2.0, 0.3, 0.9)

    # ── 需求驱动情绪梯度（第 4.3 节） ─────────────────────────────────────

    def compute_emotion_gradient(self) -> dict[str, float]:
        """计算情绪梯度：需求满足状态 → valence / arousal / dominance。

        Returns:
            {"valence": v, "arousal": a, "dominance": d}，均在 [-1, 1]。
        """
        satisfactions = [need.satisfaction for need in self.needs.values()]
        mean_sat = sum(satisfactions) / len(satisfactions) if satisfactions else 0.5

        # valence：需求满足的整体偏置
        valence = _clamp(2.0 * mean_sat - 1.0, -1.0, 1.0)

        # arousal：需求满足率的变化速度（微分信号）
        current_sats = {nt: need.satisfaction for nt, need in self.needs.items()}
        if self._prev_satisfactions is not None:
            deltas = [
                abs(current_sats[nt] - self._prev_satisfactions[nt])
                for nt in NeedType
            ]
            mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
        else:
            mean_delta = 0.0
        self._prev_satisfactions = current_sats
        arousal = _clamp(0.3 + 0.7 * mean_delta, 0.0, 1.0)

        # dominance：任务成功率
        total = self._task_success + self._task_fail
        success_rate = self._task_success / total if total else 0.5
        dominance = _clamp(0.2 + 0.8 * success_rate, 0.0, 1.0)

        return {"valence": valence, "arousal": arousal, "dominance": dominance}

    def compute_intrinsic_reward(self) -> float:
        """内在奖励：情绪改善本身也是奖励。

        公式：0.5 · valence + 0.5 · clip(valence - prev_valence, -1, 1)
        """
        gradient = self.compute_emotion_gradient()
        valence = gradient["valence"]
        prev = getattr(self, "_prev_valence", 0.0)
        self._prev_valence = valence
        improvement = _clamp(valence - prev, -1.0, 1.0)
        return 0.5 * valence + 0.5 * improvement

    # ── 序列化与状态报告 ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            nt.value: need.to_dict()
            for nt, need in self.needs.items()
        }

    def get_status(self) -> dict:
        """完整状态报告（供前端 / 日志展示）。"""
        return {
            "needs": self.to_dict(),
            "drive_vector": self.compute_drive_vector(),
            "emotion_gradient": self.compute_emotion_gradient(),
            "alpha": self.compute_dynamic_alpha(),
            "task_success": self._task_success,
            "task_fail": self._task_fail,
            "interactions": self._interaction_count,
            "tokens_consumed": self._token_consumed,
        }


__all__ = ["NeedType", "Need", "NeedDriveSystem"]
