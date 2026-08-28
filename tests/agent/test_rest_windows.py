"""Task 4.3a: 休息窗口测试

验证 active_rest_window 的跨午夜处理和自动推进间隔。
"""

from datetime import datetime, timezone


def test_rest_window_within():
    """窗口内时间应返回 is_active=True"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=22, start_minute=0, end_hour=8, end_minute=0)
    # 23:30 在 22:00-08:00 窗口内
    assert rw.is_active_at(datetime(2026, 8, 28, 23, 30, tzinfo=timezone.utc)) is True


def test_rest_window_outside():
    """窗口外时间应返回 is_active=False"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=22, start_minute=0, end_hour=8, end_minute=0)
    # 12:00 不在窗口内
    assert rw.is_active_at(datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)) is False


def test_rest_window_cross_midnight():
    """跨午夜窗口应正确处理"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=22, start_minute=0, end_hour=8, end_minute=0)
    # 凌晨 3 点在跨午夜窗口内
    assert rw.is_active_at(datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)) is True
    # 下午 3 点不在
    assert rw.is_active_at(datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)) is False


def test_rest_window_same_day():
    """非跨午夜窗口应正确处理"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=12, start_minute=0, end_hour=18, end_minute=0)
    # 14:00 在窗口内
    assert rw.is_active_at(datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)) is True
    # 20:00 不在
    assert rw.is_active_at(datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)) is False


def test_rest_window_interval():
    """窗口内自动推进间隔应在 min/max 之间"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=22, start_minute=0, end_hour=8, end_minute=0)
    interval = rw.get_advance_interval_minutes()
    assert 60 <= interval <= 240


def test_rest_window_to_dict():
    """to_dict 应返回配置"""
    from agent.proactive.rest_windows import RestWindow

    rw = RestWindow(start_hour=22, start_minute=0, end_hour=8, end_minute=0)
    d = rw.to_dict()
    assert d["start_hour"] == 22
    assert d["end_hour"] == 8
