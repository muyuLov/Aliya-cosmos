"""Agency Window 三因素主体约束

三因素门控：activityLoad / privacy / deviceAccess。
容量矩阵 + 联系候选验证 + recheck-later。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgencyWindow:
    """Agency 三因素主体约束。"""

    activity_load: float = 0.0  # 0.0(空闲) ~ 1.0(完全忙)
    privacy: bool = True  # 是否允许对外联系
    device_access: bool = True  # 设备是否可用

    def get_capacity(self) -> float:
        """计算当前容量（0.0 ~ 1.0）。

        - privacy 或 device_access 为 False → 0
        - activity_load 按非线性映射降低容量
        """
        if not self.privacy or not self.device_access:
            return 0.0
        # 非线性映射: load 0→1.0, load 0.5→0.5, load 1.0→0.0
        return max(0.0, 1.0 - self.activity_load)

    def can_contact(self) -> bool:
        """是否有足够容量发起联系。"""
        return self.get_capacity() > 0.0

    def validate_contact_candidate(
        self, motive: str, target: str
    ) -> bool:
        """验证联系候选。

        - 容量为 0 → 拒绝
        - motive 为空 → 拒绝
        - target 为空 → 拒绝
        """
        if not self.can_contact():
            return False
        if not motive or not motive.strip():
            return False
        if not target or not target.strip():
            return False
        return True

    def to_dict(self) -> dict:
        """导出状态字典。"""
        return {
            "activity_load": self.activity_load,
            "privacy": self.privacy,
            "device_access": self.device_access,
            "capacity": self.get_capacity(),
        }
