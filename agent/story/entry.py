"""剧本条目（ScriptEntry）

每条条目记录故事中发生的一件事：用户说话、角色回复、受限行动、系统事件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# 允许的条目类型
ENTRY_KINDS = frozenset({
    "user_message",
    "character_message",
    "tool_call",
    "system_event",
})


@dataclass
class ScriptEntry:
    """一条剧本条目。"""

    story_id: str
    participant_id: str
    kind: str
    actor: str
    content: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.kind not in ENTRY_KINDS:
            raise ValueError(
                f"无效的条目类型 '{self.kind}'，"
                f"应为 {sorted(ENTRY_KINDS)} 之一"
            )
