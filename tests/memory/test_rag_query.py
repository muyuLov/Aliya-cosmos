"""测试 rag_query 模块的时间表达式解析（时间类查询兜底召回）"""

from __future__ import annotations

from core.memory import rag_query


class TestExtractTimeRange:
    def test_month_only(self):
        # "7月" 应解析为当年 7 月整月范围
        result = rag_query._extract_time_range("我7月有什么活动")
        assert result is not None
        start, end = result
        assert start.endswith("-07-01")
        assert end.endswith("-07-31")

    def test_month_and_day(self):
        result = rag_query._extract_time_range("7月5号我去了哪")
        assert result is not None
        start, end = result
        assert start == end
        assert start.endswith("-07-05")

    def test_full_date(self):
        result = rag_query._extract_time_range("2026年3月15日发生了什么")
        assert result is not None
        start, end = result
        assert start == end == "2026-03-15"

    def test_full_date_dash(self):
        result = rag_query._extract_time_range("回顾一下 2026-12-25")
        assert result is not None
        start, end = result
        assert start == end == "2026-12-25"

    def test_relative_today(self):
        result = rag_query._extract_time_range("今天我做了什么")
        assert result is not None
        start, end = result
        assert start == end

    def test_no_time(self):
        assert rag_query._extract_time_range("你喜欢什么颜色") is None
