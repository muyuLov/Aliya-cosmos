"""FeelingScores — 情绪分数追踪与平滑更新

实现 Cyrene-Agent 的 smoothFeeling 算法：
- 每次更新时所有情绪分数按 decay 衰减
- 被观察到的情绪加上 observedWeight
- 快速上升情绪（担心/难过）权重更高（0.62 vs 0.3）
- 取分数最高的情绪作为当前主导情绪
"""

from __future__ import annotations

from typing import Literal

FeelingName = Literal[
    "平静", "开心", "温柔", "激动", "撒娇",
    "担心", "难过", "感动", "害羞",
]

ALL_FEELINGS: list[FeelingName] = [
    "平静", "开心", "温柔", "激动", "撒娇",
    "担心", "难过", "感动", "害羞",
]

# 快速上升情绪：观察权重更高，衰减更慢
FAST_RISE: set[FeelingName] = {"担心", "难过"}

# 普通情绪观察权重，快速上升情绪观察权重
_OBSERVE_WEIGHT_NORMAL = 0.3
_OBSERVE_WEIGHT_FAST = 0.62


class FeelingScores:
    """9 种情绪分数容器，支持衰减平滑更新。

    Usage::

        fs = FeelingScores("平静")
        fs.smooth("开心")
        fs.smooth("开心")
        fs.smooth("开心")  # 连续 3 次开心后，dominant 变为"开心"
        assert fs.dominant == "开心"
    """

    def __init__(self, initial: FeelingName = "平静") -> None:
        self._scores: dict[FeelingName, float] = {f: 0.0 for f in ALL_FEELINGS}
        self._scores[initial] = 1.0

    @property
    def scores(self) -> dict[FeelingName, float]:
        """返回当前所有情绪分数的快照（只读）。"""
        return dict(self._scores)

    @property
    def dominant(self) -> FeelingName:
        """当前分数最高的情绪。"""
        best: FeelingName = "平静"
        for f in ALL_FEELINGS:
            if self._scores[f] > self._scores[best]:
                best = f
        return best

    def smooth(self, observed: FeelingName) -> FeelingName:
        """应用衰减 + 观察权重平滑更新。

        Args:
            observed: 本轮观察到的情绪。

        Returns:
            更新后的主导情绪。
        """
        weight = _OBSERVE_WEIGHT_FAST if observed in FAST_RISE else _OBSERVE_WEIGHT_NORMAL
        decay = 1.0 - weight

        for f in ALL_FEELINGS:
            self._scores[f] *= decay
        self._scores[observed] += weight

        return self.dominant
