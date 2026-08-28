"""后台调度器重写（NarrativeScheduler）

三来源调度：
1. 自动推进（auto_advance）：定期触发生活推进
2. 到期 intent（intent_due）：延迟意图到期
3. proactive_check：主动联系重查

全部经串行队列进入主叙事；替代旧 schedule/idle 触发式逻辑。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class _IntentEntry:
    """待调度 intent 条目。"""
    intent_id: str
    summary: str
    participant_id: str
    not_before: datetime


class NarrativeScheduler:
    """后台调度器：三来源 → 事件列表。"""

    def __init__(
        self,
        *,
        auto_advance_enabled: bool = False,
        advance_interval_minutes: int = 180,
    ) -> None:
        self._auto_advance_enabled = auto_advance_enabled
        self._advance_interval_minutes = advance_interval_minutes
        self._last_advance_at: float = 0.0
        self._due_intents: list[_IntentEntry] = []
        self._proactive_checks: list[dict[str, str]] = []

    def set_last_advance_at(self, timestamp: float) -> None:
        """设置上次自动推进时间戳。"""
        self._last_advance_at = timestamp

    def set_advance_interval_minutes(self, minutes: int) -> None:
        """设置自动推进间隔。"""
        self._advance_interval_minutes = minutes

    def add_intent(
        self,
        intent_id: str,
        summary: str,
        participant_id: str,
        not_before: datetime,
    ) -> None:
        """注册到期 intent。"""
        self._due_intents.append(_IntentEntry(
            intent_id=intent_id,
            summary=summary,
            participant_id=participant_id,
            not_before=not_before,
        ))

    def add_proactive_check(
        self, story_id: str, participant_id: str, reason: str
    ) -> None:
        """注册 proactive-check 候选。"""
        self._proactive_checks.append({
            "story_id": story_id,
            "participant_id": participant_id,
            "reason": reason,
        })

    async def tick(self) -> list[dict[str, Any]]:
        """扫描一次，返回到期事件列表。"""
        events: list[dict[str, Any]] = []

        # 1. 到期 intent
        now = datetime.now(timezone.utc)
        remaining: list[_IntentEntry] = []
        for entry in self._due_intents:
            if entry.not_before <= now:
                events.append({
                    "type": "intent_due",
                    "intent_id": entry.intent_id,
                    "summary": entry.summary,
                    "participant_id": entry.participant_id,
                })
            else:
                remaining.append(entry)
        self._due_intents = remaining

        # 2. 自动推进
        if self._auto_advance_enabled:
            now_ts = time.time()
            interval_s = self._advance_interval_minutes * 60
            if now_ts - self._last_advance_at >= interval_s:
                events.append({
                    "type": "auto_advance",
                    "timestamp": now.isoformat(),
                })
                self._last_advance_at = now_ts

        # 3. proactive-check
        if self._proactive_checks:
            for check in self._proactive_checks:
                events.append({
                    "type": "proactive_check",
                    "story_id": check["story_id"],
                    "participant_id": check["participant_id"],
                    "reason": check["reason"],
                })
            self._proactive_checks.clear()

        return events
