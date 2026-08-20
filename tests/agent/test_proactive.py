"""测试主动聊天 Part B：scheduler（触发器 + 护栏 + 调度循环）"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from agent.proactive.scheduler import (
    ProactiveConfig,
    ProactiveScheduler,
    TriggerConfig,
    _in_quiet_hours,
    create_proactive_scheduler,
)


# ── TriggerConfig / ProactiveConfig ───────────────────────────


class TestTriggerConfig:
    def test_schedule_trigger(self):
        tc = TriggerConfig(type="schedule", at="20:00", message="晚上好")
        assert tc.type == "schedule"
        assert tc.at == "20:00"

    def test_idle_trigger(self):
        tc = TriggerConfig(type="idle", message="在忙吗？")
        assert tc.type == "idle"
        assert tc.at is None


# ── quiet_hours ──────────────────────────────────────────────


class TestQuietHours:
    def test_in_quiet_hours_normal(self):
        # 23:30 在 23:00~07:00 跨午夜区间内
        now = datetime(2026, 8, 20, 23, 30)
        assert _in_quiet_hours(now, "23:00", "07:00") is True

    def test_not_in_quiet_hours(self):
        now = datetime(2026, 8, 20, 12, 0)
        assert _in_quiet_hours(now, "23:00", "07:00") is False

    def test_in_quiet_hours_boundary(self):
        # 07:00 是安静时段的边界
        now = datetime(2026, 8, 20, 7, 0)
        assert _in_quiet_hours(now, "23:00", "07:00") is True

    def test_same_day_quiet_hours(self):
        # 09:00 ~ 17:00 同日区间
        now = datetime(2026, 8, 20, 10, 0)
        assert _in_quiet_hours(now, "09:00", "17:00") is True
        now2 = datetime(2026, 8, 20, 18, 0)
        assert _in_quiet_hours(now2, "09:00", "17:00") is False


# ── can_trigger ──────────────────────────────────────────────


class TestCanTrigger:
    def _make_scheduler(self, **overrides) -> ProactiveScheduler:
        enabled = overrides.pop("enabled", True)
        check_interval_seconds = overrides.pop("check_interval_seconds", 60)
        quiet_hours_start = overrides.pop("quiet_hours_start", "23:00")
        quiet_hours_end = overrides.pop("quiet_hours_end", "23:01")
        idle_timeout_minutes = overrides.pop("idle_timeout_minutes", 30)
        max_unanswered = overrides.pop("max_unanswered", 3)
        cfg = ProactiveConfig(
            enabled=enabled,
            check_interval_seconds=check_interval_seconds,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            idle_timeout_minutes=idle_timeout_minutes,
            max_unanswered=max_unanswered,
        )
        return ProactiveScheduler(cfg, AsyncMock())

    def test_disabled_cannot_trigger(self):
        s = self._make_scheduler(enabled=False)
        tc = TriggerConfig(type="idle", message="hi")
        assert s.can_trigger(tc) is False

    def test_in_quiet_hours_cannot_trigger(self):
        s = self._make_scheduler(quiet_hours_start="00:00", quiet_hours_end="23:59")
        tc = TriggerConfig(type="idle", message="hi")
        now = datetime(2026, 8, 20, 12, 0)
        assert s.can_trigger(tc, now) is False

    def test_processing_cannot_trigger(self):
        s = self._make_scheduler()
        s._processing = True
        tc = TriggerConfig(type="idle", message="hi")
        assert s.can_trigger(tc) is False

    def test_max_unanswered_cannot_trigger(self):
        s = self._make_scheduler(max_unanswered=3)
        s._triggered_count = 3
        tc = TriggerConfig(type="idle", message="hi")
        assert s.can_trigger(tc) is False

    def test_idle_can_trigger_when_timeout(self):
        s = self._make_scheduler(idle_timeout_minutes=30)
        s._last_user_message_time = datetime(2026, 8, 20, 10, 0)
        tc = TriggerConfig(type="idle", message="hi")
        now = datetime(2026, 8, 20, 11, 0)  # 1 小时后
        assert s.can_trigger(tc, now) is True

    def test_idle_cannot_trigger_before_timeout(self):
        s = self._make_scheduler(idle_timeout_minutes=30)
        s._last_user_message_time = datetime(2026, 8, 20, 10, 0)
        tc = TriggerConfig(type="idle", message="hi")
        now = datetime(2026, 8, 20, 10, 10)  # 10 分钟后
        assert s.can_trigger(tc, now) is False

    def test_schedule_can_trigger_at_time(self):
        s = self._make_scheduler()
        tc = TriggerConfig(type="schedule", at="20:00", message="晚上好")
        now = datetime(2026, 8, 20, 20, 0)
        assert s.can_trigger(tc, now) is True

    def test_schedule_cannot_trigger_wrong_time(self):
        s = self._make_scheduler()
        tc = TriggerConfig(type="schedule", at="20:00", message="晚上好")
        now = datetime(2026, 8, 20, 19, 0)
        assert s.can_trigger(tc, now) is False

    def test_schedule_cannot_trigger_twice_same_day(self):
        s = self._make_scheduler()
        tc = TriggerConfig(type="schedule", at="20:00", message="晚上好")
        now = datetime(2026, 8, 20, 20, 0)
        assert s.can_trigger(tc, now) is True
        # 同一天同一时间第二次
        assert s.can_trigger(tc, now) is False


# ── notify_user_message ──────────────────────────────────────


class TestNotifyUserMessage:
    @pytest.mark.asyncio
    async def test_notify_resets_count(self):
        sink = AsyncMock()
        s = ProactiveScheduler(ProactiveConfig(enabled=True), sink)
        s._triggered_count = 2
        await s.notify_user_message("session1")
        assert s._triggered_count == 0
        assert s._last_user_message_time is not None


# ── schedule loop (mock) ─────────────────────────────────────


class TestSchedulerLoop:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        sink = AsyncMock()
        cfg = ProactiveConfig(enabled=True, check_interval_seconds=1000)
        s = ProactiveScheduler(cfg, sink)
        await s.start()
        assert s._task is not None
        await s.stop()
        assert s._task is None

    @pytest.mark.asyncio
    async def test_set_enabled(self):
        sink = AsyncMock()
        cfg = ProactiveConfig(enabled=False, check_interval_seconds=1000)
        s = ProactiveScheduler(cfg, sink)
        await s.set_enabled(True)
        assert s.enabled is True
        assert s._task is not None
        await s.set_enabled(False)
        assert s.enabled is False
        assert s._task is None


# ── factory ──────────────────────────────────────────────────


class TestCreateProactiveScheduler:
    def test_from_config_dict(self):
        config = {
            "proactive": {
                "enabled": True,
                "check_interval_seconds": 30,
                "quiet_hours": {"start": "22:00", "end": "08:00"},
                "idle_timeout_minutes": 20,
                "triggers": [
                    {"type": "schedule", "at": "19:00", "message": "该吃饭了"},
                    {"type": "idle", "message": "在忙吗？"},
                ],
            }
        }
        sink = AsyncMock()
        s = create_proactive_scheduler(config, sink)
        assert s.enabled is True
        assert len(s._config.triggers) == 2
        assert s._config.triggers[0].at == "19:00"
        assert s._config.quiet_hours_start == "22:00"

    def test_defaults(self):
        config = {}
        sink = AsyncMock()
        s = create_proactive_scheduler(config, sink)
        assert s.enabled is False
        assert s._config.check_interval_seconds == 60
        assert s._config.idle_timeout_minutes == 30
