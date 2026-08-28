"""时间处理模块：双时钟（UTC 存储 / 本地渲染）、时段、休息窗口、日历日键。

按 HDS-Interlude 时间机制实现（设计文档 3.9）：
- DB 全存 ISO-8601 UTC，本地时间仅在渲染时转换。
- 时段分界：morning 5-12 / afternoon 12-18 / evening 18-22 / night。
- 休息窗口用半开区间，跨午夜时拆分为 ``[start, 24h) || [0h, end)``。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 默认本地时区（可经 resolve_timezone 试探回退）
DEFAULT_TIMEZONE = "Asia/Shanghai"
UTC_TZ = timezone.utc

# 时区分界（闭区间），依据设计文档
_PERIOD_BOUNDARIES: list[tuple[str, int, int]] = [
    ("morning", 5, 12),
    ("afternoon", 12, 18),
    ("evening", 18, 22),
]


def _now_utc() -> datetime:
    return datetime.now(UTC_TZ)


@lru_cache(maxsize=32)
def _cached_zoneinfo(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def resolve_timezone(candidate: str | None = None) -> str:
    """试探候选时区名，失败回退 ``UTC``；未提供候选时使用默认 ``Asia/Shanghai``。

    结果缓存以避免反复构造 ``ZoneInfo``。
    """
    name = candidate or DEFAULT_TIMEZONE
    try:
        _cached_zoneinfo(name)
        return name
    except ZoneInfoNotFoundError:
        return "UTC"


def _to_local(value: datetime | str, timezone_name: str) -> datetime:
    """将 ISO-8601 字符串或 datetime 转换为本地时区带时区的时刻。"""
    if isinstance(value, str):
        # 统一补全时区：无偏移视为 UTC
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
    else:
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(_cached_zoneinfo(timezone_name))


def local_clock_minutes(value: datetime | str, timezone_name: str) -> int:
    """将时刻转换为本地时区后提取 hour/minute 换算为分钟（0-1439）。"""
    local = _to_local(value, timezone_name)
    return local.hour * 60 + local.minute


def story_local_time_context(
    value: datetime | str | None = None, timezone_name: str | None = None
) -> dict[str, int | bool | str]:
    """返回主叙事所需的时间端点：hour/period/daylight_expectation/weekday/offset。

    返回字段：
    - ``hour``: 本地小时（int）
    - ``period``: morning/afternoon/evening/night
    - ``daylight_expectation``: 是否通常有日照（morning/afternoon 为 True）
    - ``weekday``: 星期（0=周一 ... 6=周日，Python weekday() 语义）
    - ``offset_minutes``: 相对 UTC 的分钟偏移
    """
    tz = resolve_timezone(timezone_name)
    local = _to_local(value or _now_utc(), tz)
    period = _period_of_hour(local.hour)
    daylight = period in ("morning", "afternoon")
    utcoff = local.utcoffset()
    offset_minutes = int(utcoff.total_seconds() // 60) if utcoff else 0
    return {
        "hour": local.hour,
        "period": period,
        "daylight_expectation": daylight,
        "weekday": local.weekday(),
        "offset_minutes": offset_minutes,
    }


def _period_of_hour(hour: int) -> str:
    for name, start, end in _PERIOD_BOUNDARIES:
        if start <= hour < end:
            return name
    return "night"


def _parse_clock(s: str) -> int:
    """解析 ``HH:MM`` 为当天分钟数（0-1439）。"""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s.strip())
    if not m:
        raise ValueError(f"无效时钟格式: {s!r}，应为 HH:MM")
    return int(m.group(1)) * 60 + int(m.group(2))


def active_rest_window(
    rest_windows: list[tuple[str, str]] | list[list[str]],
    timezone_name: str,
    now: datetime | str | None = None,
) -> bool:
    """判断当前本地时间是否处于任一休息窗口内。

    窗口为 ``(start, end)`` 字符串对（``HH:MM``）。半开区间：
    - 常规（start<=end）：``localMinutes >= start and localMinutes < end``。
    - 跨午夜（start>end）：``localMinutes >= start or localMinutes < end``。
    """
    tz = resolve_timezone(timezone_name)
    local = _to_local(now or _now_utc(), tz)
    minutes = local.hour * 60 + local.minute
    for window in rest_windows or []:
        start, end = _parse_clock(window[0]), _parse_clock(window[1])
        if start <= end:
            if start <= minutes < end:
                return True
        else:
            # 跨午夜：localMinutes 落在 [start, 24h) 或 [0h, end)
            if minutes >= start or minutes < end:
                return True
    return False


def calendar_day_key(value: datetime | str | None = None, timezone_name: str | None = None) -> str:
    """返回本地时区的日历日键 ``YYYY-MM-DD``（Overlay 证据天数/分组键）。"""
    tz = resolve_timezone(timezone_name)
    local = _to_local(value or _now_utc(), tz)
    return local.strftime("%Y-%m-%d")
