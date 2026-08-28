"""参与者（Participant）

每个参与者持有独立的资料、关系、演化状态。
一个故事中可以有多个参与者（AI 角色 + 用户），彼此隔离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Participant:
    """参与者资料与演化状态。"""

    story_id: str
    person_id: str
    display_name: str
    profile: str = ""
    relationship: str = ""
    relationship_overlay: dict = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    last_user_message_at: datetime | None = None
    last_character_message_at: datetime | None = None
    unread_message_count: int = 0
    pending_reply_count: int = 0
    status: str = "active"  # active | inactive
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
