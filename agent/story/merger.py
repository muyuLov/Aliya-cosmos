"""消息合并与过期请求取消（Merger）

按设计 3.11：
- 同一关系分支连续消息在 mergeWindowMs（默认 2 秒）内合并
- should_supersede_narrative_request：首条回复提交前新输入接管本轮并重写
- 首条提交后截断未发送后续气泡作为未完成意图
- 过期模型结果不落库
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MERGE_WINDOW_MS = 2000


@dataclass
class _PendingEntry:
    """待合并消息条目。"""
    story_id: str
    participant_id: str
    content: str
    pushed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    committed: bool = False


class MessageMerger:
    """消息短时合并与过期请求取消。"""

    def __init__(self, merge_window_ms: int = _DEFAULT_MERGE_WINDOW_MS) -> None:
        self._merge_window_ms = merge_window_ms
        # (story_id, participant_id) → 最近待合并条目
        self._pending: dict[tuple[str, str], _PendingEntry] = {}

    async def push(
        self, story_id: str, participant_id: str, content: str
    ) -> str | None:
        """推入消息。

        如果在合并窗口内且同参与者，合并返回合并文本。
        否则返回 None（调用方应作为独立消息处理）。
        """
        key = (story_id, participant_id)
        now = datetime.now(timezone.utc)
        existing = self._pending.get(key)

        if existing is not None and not existing.committed:
            elapsed_ms = (now - existing.pushed_at).total_seconds() * 1000
            if elapsed_ms <= self._merge_window_ms:
                # 合并
                merged = f"{existing.content}\n{content}"
                self._pending[key] = _PendingEntry(
                    story_id=story_id,
                    participant_id=participant_id,
                    content=merged,
                    pushed_at=now,
                )
                return merged

        # 新条目或超出窗口
        self._pending[key] = _PendingEntry(
            story_id=story_id,
            participant_id=participant_id,
            content=content,
            pushed_at=now,
        )
        return None

    def has_pending(self, story_id: str, participant_id: str) -> bool:
        """检查是否有未提交的待合并消息。"""
        key = (story_id, participant_id)
        entry = self._pending.get(key)
        return entry is not None and not entry.committed

    async def should_supersede(
        self, story_id: str, participant_id: str
    ) -> bool:
        """检查是否应取消待处理请求（新输入在提交前到达）。"""
        return self.has_pending(story_id, participant_id)

    def mark_committed(self, story_id: str, participant_id: str) -> None:
        """标记消息已提交（回复已生成）。"""
        key = (story_id, participant_id)
        entry = self._pending.get(key)
        if entry is not None:
            entry.committed = True

    async def is_stale(
        self, story_id: str, participant_id: str, *, tolerance_ms: int = 0
    ) -> bool:
        """检查结果是否已过期。"""
        key = (story_id, participant_id)
        entry = self._pending.get(key)
        if entry is None:
            return True
        now = datetime.now(timezone.utc)
        elapsed_ms = (now - entry.pushed_at).total_seconds() * 1000
        return elapsed_ms > tolerance_ms

    def clear(self, story_id: str, participant_id: str) -> None:
        """清除待合并条目。"""
        key = (story_id, participant_id)
        self._pending.pop(key, None)
