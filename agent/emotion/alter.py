"""Alter 动态氛围阈值系统

纯状态机（无 LLM 依赖的核心），持有累计值/权重/方向/历史。
达到阈值仅记录待分析标记，可见回复不等待侧端模型。

核心概念：
- cumulative: 累计氛围偏移值
- direction: 当前氛围方向（warm/cool/...）
- weight: 同向权重（同向增强、反向衰减、过低清除）
- 阈值: base_threshold × (1 - density × factor)，密度高时阈值降低
- 冷却: 触发后 cooldown_seconds 内不重复触发
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# 默认参数
_DEFAULT_BASE_THRESHOLD = 5.0
_DEFAULT_DENSITY_FACTOR = 0.5
_DEFAULT_COOLDOWN_SECONDS = 300  # 5 分钟
_WEIGHT_DECAY_THRESHOLD = 0.05  # 权重低于此清除


@dataclass
class AlterState:
    """Alter 动态氛围状态机。"""

    cumulative: float = 0.0
    direction: str = ""
    weight: float = 0.0
    base_threshold: float = _DEFAULT_BASE_THRESHOLD
    density_factor: float = _DEFAULT_DENSITY_FACTOR
    cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS
    _last_triggered_at: float = 0.0
    _history: list[dict] = field(default_factory=list)

    def apply(self, delta: float, direction: str) -> None:
        """应用氛围 delta。

        - 同向: 累加 + 增强权重
        - 反向: 累减 + 衰减权重
        - 完全抵消: 清除方向和权重
        """
        if direction == self.direction:
            # 同向增强
            self.cumulative += delta
            self.weight = min(1.0, self.weight + abs(delta) * 0.2)
        elif self.direction and direction != self.direction:
            # 反向衰减
            self.cumulative += delta
            self.weight = max(0.0, self.weight - abs(delta) * 0.3)
        else:
            # 新方向
            self.cumulative += delta
            self.direction = direction
            self.weight = min(1.0, abs(delta) * 0.3)

        # 完全抵消后清除
        if abs(self.cumulative) < 0.01:
            self.cumulative = 0.0
            self.direction = ""
            self.weight = 0.0

    def get_threshold(self, density: float = 0.0) -> float:
        """获取动态阈值：density 越高，阈值越低。"""
        clamped_density = max(0.0, min(1.0, density))
        return self.base_threshold * (1.0 - clamped_density * self.density_factor)

    def should_trigger(self, density: float = 0.0) -> bool:
        """检查是否应触发侧端分析。"""
        now = time.time()
        # 冷却检查
        if now - self._last_triggered_at < self.cooldown_seconds:
            return False
        # 阈值检查
        threshold = self.get_threshold(density)
        return abs(self.cumulative) >= threshold

    def mark_triggered(self) -> None:
        """标记已触发（更新冷却时间戳）。"""
        self._last_triggered_at = time.time()
        self._history.append({
            "cumulative": self.cumulative,
            "direction": self.direction,
            "weight": self.weight,
            "timestamp": self._last_triggered_at,
        })

    def update_weight_decay(self) -> None:
        """权重自然衰减（时间推移）。过低时清除。"""
        if abs(self.cumulative) < 0.01:
            self.weight = 0.0
            self.direction = ""
            return
        if self.weight < _WEIGHT_DECAY_THRESHOLD:
            self.weight = 0.0
            self.direction = ""

    def to_dict(self) -> dict:
        """导出状态字典。"""
        return {
            "cumulative": self.cumulative,
            "direction": self.direction,
            "weight": self.weight,
            "threshold": self.get_threshold(),
            "history_count": len(self._history),
        }
