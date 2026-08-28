from datetime import datetime, timezone

from core.time import (
    active_rest_window,
    calendar_day_key,
    local_clock_minutes,
    resolve_timezone,
    story_local_time_context,
)


def _utc(hour: int, minute: int = 0, day: int = 28) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def test_resolve_timezone_valid():
    assert resolve_timezone("Asia/Shanghai") == "Asia/Shanghai"


def test_resolve_timezone_invalid_falls_back_to_utc():
    assert resolve_timezone("Not/AZone") == "UTC"


def test_resolve_timezone_default_is_shanghai():
    # 默认（无候选）使用 Asia/Shanghai
    assert resolve_timezone() == "Asia/Shanghai"


def test_local_clock_minutes_converts_to_local():
    # UTC 03:00 → 上海 11:00 → 660 分钟
    assert local_clock_minutes(_utc(3), "Asia/Shanghai") == 660


def test_story_local_time_context_periods():
    ctx_morning = story_local_time_context(_utc(3), "Asia/Shanghai")  # 上海 11:00
    assert ctx_morning["period"] == "morning"
    assert ctx_morning["hour"] == 11

    # 上海 13:00 → afternoon
    ctx_afternoon = story_local_time_context(_utc(5), "Asia/Shanghai")
    assert ctx_afternoon["period"] == "afternoon"

    # 上海 20:00 → evening
    ctx_evening = story_local_time_context(_utc(12), "Asia/Shanghai")
    assert ctx_evening["period"] == "evening"

    # 上海 23:00 → night
    ctx_night = story_local_time_context(_utc(15), "Asia/Shanghai")
    assert ctx_night["period"] == "night"


def test_story_local_time_context_fields():
    ctx = story_local_time_context(_utc(3), "Asia/Shanghai")
    assert set(ctx) == {"hour", "period", "daylight_expectation", "weekday", "offset_minutes"}
    assert isinstance(ctx["daylight_expectation"], bool)
    assert ctx["weekday"] in range(7)
    assert ctx["offset_minutes"] == 480  # UTC+8


def test_active_rest_window_within_same_day():
    windows = [("23:00", "07:00")]
    # 上海 01:00 处于跨午夜休息窗口内
    assert active_rest_window(windows, "Asia/Shanghai", _utc(17, day=27)) is True
    # 上海 12:00 不在窗口内
    assert active_rest_window(windows, "Asia/Shanghai", _utc(4)) is False


def test_active_rest_window_normal_range():
    windows = [("12:00", "14:00")]
    # 上海 13:00 在窗口内
    assert active_rest_window(windows, "Asia/Shanghai", _utc(5)) is True
    # 上海 15:00 在窗口外
    assert active_rest_window(windows, "Asia/Shanghai", _utc(7)) is False


def test_active_rest_window_empty():
    assert active_rest_window([], "Asia/Shanghai", _utc(3)) is False


def test_calendar_day_key():
    # UTC 17:00 8/27 → 上海 01:00 8/28
    assert calendar_day_key(_utc(17, day=27), "Asia/Shanghai") == "2026-08-28"
