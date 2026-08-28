"""OverlayLayer（L1）：演化人设/关系/世界观的证据链层。

每个 StatePatch 提案需达到证据门槛才被应用：
- 普通变化：置信度 ≥ confidence_threshold 且回合数 ≥ min_turns 且天数 ≥ min_days；
- 重大变化（major）：置信度 ≥ 0.95；
- 同路径（target + proposed_value）冷却 cooldown_hours。
证据按 source_entry_id 去重计回合、按日历日（YYYY-MM-DD）计天数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.memory.layers import MemoryEntry, MemoryLayer

# 重大变化的置信度硬门槛
_MAJOR_CONFIDENCE = 0.95


@dataclass
class StatePatch:
    """一个人设/关系/世界观的演化提案（证据链）。"""

    id: str
    target: str
    proposed_value: str
    evidence: str
    confidence: float
    impact: str  # minor | major
    source_entry_ids: list[str] = field(default_factory=list)
    status: str = "proposed"  # proposed | applied | rejected
    created_at: str = ""
    applied_at: str | None = None


class OverlayLayer(MemoryLayer):
    """证据链演化层。"""

    name = "overlay"

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.82,
        min_turns: int = 3,
        min_days: int = 2,
        cooldown_hours: float = 72,
    ) -> None:
        self._confidence_threshold = confidence_threshold
        self._min_turns = min_turns
        self._min_days = min_days
        self._cooldown_hours = cooldown_hours
        # patch_id → 证据 source_entry_id 集合（计回合）
        self._evidence: dict[str, set[str]] = {}
        # patch_id → 日历日集合（计天数）
        self._evidence_days: dict[str, set[str]] = {}
        # path key → 上次应用时间（冷却用）
        self._last_applied_at: dict[str, datetime] = {}

    def record_evidence(self, patch_id: str, source_entry_id: str, day: str) -> None:
        """记录一条证据：按 source_entry_id 去重计回合，按 day 计日历日。"""
        self._evidence.setdefault(patch_id, set()).add(source_entry_id)
        self._evidence_days.setdefault(patch_id, set()).add(day)

    def _meets_threshold(self, patch: StatePatch) -> bool:
        if patch.impact == "major":
            return patch.confidence >= _MAJOR_CONFIDENCE
        return patch.confidence >= self._confidence_threshold

    def _meets_evidence(self, patch_id: str) -> bool:
        turns = len(self._evidence.get(patch_id, set()))
        days = len(self._evidence_days.get(patch_id, set()))
        return turns >= self._min_turns and days >= self._min_days

    def _cooldown_ok(self, patch: StatePatch) -> bool:
        path = f"{patch.target}:{patch.proposed_value}"
        last = self._last_applied_at.get(path)
        if last is None:
            return True
        try:
            created = datetime.fromisoformat(patch.created_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        delta_hours = (created - last).total_seconds() / 3600
        return delta_hours >= self._cooldown_hours

    async def try_apply(self, patch: StatePatch) -> bool:
        """按门槛裁决是否应用提案；成功则置状态并记录冷却。"""
        if patch.status == "applied":
            return True
        if not self._meets_threshold(patch):
            patch.status = "rejected"
            return False
        if not self._meets_evidence(patch.id):
            patch.status = "rejected"
            return False
        if not self._cooldown_ok(patch):
            patch.status = "rejected"
            return False
        patch.status = "applied"
        patch.applied_at = patch.created_at or datetime.now().isoformat()
        self._last_applied_at[f"{patch.target}:{patch.proposed_value}"] = datetime.now()
        return True

    async def write(self, entry: MemoryEntry) -> None:
        # Overlay 不直接写入文本条目，演化经 StatePatch 应用
        return None

    async def query(self, text: str, limit: int = 5) -> list[MemoryEntry]:
        return []

    async def forget(self, entry_id: str) -> None:
        return None

    async def decay(self, factor: float = 0.95) -> None:
        return None
