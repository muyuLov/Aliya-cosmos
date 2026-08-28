"""Canonical Story（主剧本状态）

每个会话对应一个 Canonical Story，持有场景设定、故事游标、活跃状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class CanonicalStory:
    """主剧本状态。"""

    story_id: str
    setting: str = ""
    state: dict = field(default_factory=dict)
    cursor_at: Optional[datetime] = None
    status: str = "active"  # active | paused | ended
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
