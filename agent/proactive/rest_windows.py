"""休息窗口（Rest Window）

约束自动推进间隔。跨午夜用半开区间：
- start <= end: localMinutes >= start && < end
- start > end (跨午夜): localMinutes >= start || < end
"""

from __future__ import annotations

import random
from datetime import datetime, timezone


class RestWindow:
    """休息窗口约束。"""

    def __init__(
        self,
        *,
        start_hour: int = 22,
        start_minute: int = 0,
        end_hour: int = 8,
        end_minute: int = 0,
        min_interval_minutes: int = 60,
        max_interval_minutes: int = 240,
    ) -> None:
        self.start_hour = start_hour
        self.start_minute = start_minute
        self.end_hour = end_hour
        self.end_minute = end_minute
        self.min_interval_minutes = min_interval_minutes
        self.max_interval_minutes = max_interval_minutes

    def is_active_at(self, dt: datetime) -> bool:
        """检查指定时间是否在窗口内。"""
        minutes = dt.hour * 60 + dt.minute
        start = self.start_hour * 60 + self.start_minute
        end = self.end_hour * 60 + self.end_minute

        if start <= end:
            # 同日窗口
            return start <= minutes < end
        else:
            # 跨午夜窗口
            return minutes >= start or minutes < end

    def get_advance_interval_minutes(self) -> int:
        """窗口内自动推进间隔（随机 min~max）。"""
        return random.randint(self.min_interval_minutes, self.max_interval_minutes)

    def to_dict(self) -> dict:
        """导出配置。"""
        return {
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "end_hour": self.end_hour,
            "end_minute": self.end_minute,
            "min_interval_minutes": self.min_interval_minutes,
            "max_interval_minutes": self.max_interval_minutes,
        }
