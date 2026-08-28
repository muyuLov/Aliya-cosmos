"""场景/弧线管理

SceneEntry / Scene / Arc，阈值触发关闭 + LLM 压缩。
每个场景持有条目列表，达到阈值（条目数/字符数）自动关闭，
关闭时生成摘要压缩为 ContinuityLayer 记忆。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# 场景条目类型
SCENE_ENTRY_KINDS = frozenset({
    "user_message",
    "ai_reply",
    "tool_call",
    "system_event",
})

# 默认阈值
_DEFAULT_ENTRY_THRESHOLD = 16
_DEFAULT_CHAR_THRESHOLD = 10000


@dataclass
class SceneEntry:
    """场景条目。"""
    id: int
    kind: str
    content: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Scene:
    """场景：一组连续的剧本条目。"""
    scene_id: str
    arc_id: str = ""
    status: str = "active"  # active | closed
    hook: str = ""
    summary: str = ""
    entries: list[SceneEntry] = field(default_factory=list)
    entry_threshold: int = _DEFAULT_ENTRY_THRESHOLD
    char_threshold: int = _DEFAULT_CHAR_THRESHOLD
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def char_count(self) -> int:
        return sum(len(e.content) for e in self.entries)

    def add_entry(self, entry: SceneEntry) -> None:
        self.entries.append(entry)

    def should_close(self) -> bool:
        """检查是否达到关闭阈值。"""
        return self.entry_count >= self.entry_threshold or self.char_count >= self.char_threshold

    def close(self, summary: str = "") -> None:
        """关闭场景。"""
        self.status = "closed"
        self.summary = summary
        self.closed_at = datetime.now(timezone.utc)

    def get_entries_since(self, since: datetime) -> list[SceneEntry]:
        """获取指定时间之后的条目。"""
        return [e for e in self.entries if e.occurred_at >= since]


@dataclass
class Arc:
    """弧线：一组场景的集合，承载更长时间尺度的叙事。"""
    arc_id: str
    theme: str = ""
    status: str = "active"  # active | completed
    scenes: list[Scene] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_scene(self, scene: Scene) -> None:
        self.scenes.append(scene)

    def close_scene(self, scene_id: str, summary: str = "") -> None:
        """关闭指定场景。"""
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                scene.close(summary)
                break

    def get_active_scene(self) -> Optional[Scene]:
        """获取当前活跃场景。"""
        for scene in self.scenes:
            if scene.status == "active":
                return scene
        return None
