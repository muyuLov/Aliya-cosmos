"""测试 rag_query 模块的时间表达式解析（时间类查询兜底召回）"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock, patch

from core.memory import rag_query


class MockResponse:
    """模拟 LLM 响应对象"""

    def __init__(self, content: str):
        self.content = content


class _FakeConfig:
    """最小 GRAG 配置（仅提供回答生成所需的 extractor 配置）"""

    def __init__(self):
        self.extractor = Mock(max_retries=1, timeout=5)
        self.similarity_threshold = 0.0
        self.context_length = 10
        self.user_name = "用户"
        self.ai_name = "Aliya"


class TestGenerateAnswerWithSource:
    """回答生成对含来源文本（6 元素）五元组的兼容"""

    @pytest.mark.asyncio
    async def test_six_tuple_includes_source(self):
        from core.memory.rag_query import RAGQueryEngine

        engine = RAGQueryEngine()
        mock_chat = AsyncMock(return_value=MockResponse("回答"))
        quintuples = [
            ("Aliya", "人物", "喜欢", "咖啡", "物品", "用户说：我喜欢喝咖啡"),
        ]
        with (
            patch.object(
                engine.provider, "async_chat_completion", new=mock_chat,
            ),
            patch(
                "core.memory.rag_query.get_grag_config",
                return_value=_FakeConfig(),
            ),
        ):
            result = await engine._generate_answer("你喜欢什么", quintuples)
            assert result == "回答"
            # 验证 prompt 中包含来源文本
            call_args = mock_chat.call_args
            prompt = call_args[0][0].messages[0]["content"]
            assert "来源：用户说：我喜欢喝咖啡" in prompt
            assert "Aliya(人物) —[喜欢]-> 咖啡(物品)" in prompt

    @pytest.mark.asyncio
    async def test_five_tuple_no_source(self):
        from core.memory.rag_query import RAGQueryEngine

        engine = RAGQueryEngine()
        mock_chat = AsyncMock(return_value=MockResponse("回答"))
        quintuples = [("Aliya", "人物", "喜欢", "咖啡", "物品")]
        with (
            patch.object(
                engine.provider, "async_chat_completion", new=mock_chat,
            ),
            patch(
                "core.memory.rag_query.get_grag_config",
                return_value=_FakeConfig(),
            ),
        ):
            result = await engine._generate_answer("你喜欢什么", quintuples)
            assert result == "回答"
            call_args = mock_chat.call_args
            prompt = call_args[0][0].messages[0]["content"]
            assert "来源：" not in prompt

    @pytest.mark.asyncio
    async def test_empty_source_omitted(self):
        from core.memory.rag_query import RAGQueryEngine

        engine = RAGQueryEngine()
        mock_chat = AsyncMock(return_value=MockResponse("回答"))
        quintuples = [("Aliya", "人物", "喜欢", "咖啡", "物品", "")]
        with (
            patch.object(
                engine.provider, "async_chat_completion", new=mock_chat,
            ),
            patch(
                "core.memory.rag_query.get_grag_config",
                return_value=_FakeConfig(),
            ),
        ):
            await engine._generate_answer("你喜欢什么", quintuples)
            call_args = mock_chat.call_args
            prompt = call_args[0][0].messages[0]["content"]
            assert "来源：" not in prompt


class TestRetrievalLogging:
    """检索链路日志展示（命中关系 + 来源文本逐条记录）"""

    @pytest.mark.asyncio
    async def test_query_logs_retrieved_quintuples(self):
        from core.memory.rag_query import RAGQueryEngine

        engine = RAGQueryEngine()
        engine._recent_context = []
        mock_chat = AsyncMock(return_value=MockResponse("回答"))
        mock_extract = AsyncMock(return_value=["咖啡"])
        quintuples = [
            ("Aliya", "人物", "喜欢", "咖啡", "物品", "用户说：我喜欢喝咖啡"),
            ("Aliya", "人物", "讨厌", "茶", "物品"),
        ]
        with (
            patch.object(engine, "_extract_keywords", new=mock_extract),
            patch.object(
                engine.provider, "async_chat_completion", new=mock_chat,
            ),
            patch(
                "core.memory.rag_query.graph.query_graph_by_keywords",
                return_value=quintuples,
            ),
            patch(
                "core.memory.rag_query.get_grag_config",
                return_value=_FakeConfig(),
            ),
            patch("core.memory.rag_query.logger") as mock_logger,
        ):
            result = await engine.query_async("咖啡")
            assert result == "回答"

            # 断言关键日志：命中总数 + 逐条关系（含来源）
            # 参数化日志：args[0] 为格式串，args[1:] 为值
            hit_calls = [c for c in mock_logger.info.call_args_list]
            assert any(
                c.args[0] == "图谱检索命中 %d 条关系（关键词: %s）" and c.args[1] == 2
                for c in hit_calls
            )
            # 只格式化"检索命中"开头的日志（格式串与参数一一对应，安全）
            formatted = [
                (c.args[0] % c.args[1:])
                for c in hit_calls
                if c.args[0].startswith("  检索命中")
            ]
            assert any("Aliya(人物) —[喜欢]-> 咖啡(物品)" in m and "来源: 用户说：我喜欢喝咖啡" in m for m in formatted)
            assert any("Aliya(人物) —[讨厌]-> 茶(物品)" in m for m in formatted)

    @pytest.mark.asyncio
    async def test_query_logs_no_hits(self):
        from core.memory.rag_query import RAGQueryEngine

        engine = RAGQueryEngine()
        engine._recent_context = []
        mock_extract = AsyncMock(return_value=["咖啡"])
        with (
            patch.object(engine, "_extract_keywords", new=mock_extract),
            patch(
                "core.memory.rag_query.graph.query_graph_by_keywords",
                return_value=[],
            ),
            patch(
                "core.memory.rag_query.get_grag_config",
                return_value=_FakeConfig(),
            ),
            patch("core.memory.rag_query.logger") as mock_logger,
        ):
            result = await engine.query_async("咖啡")
            assert result is None
            info_msgs = [str(c.args[0]) for c in mock_logger.info.call_args_list]
            assert any("图谱中未找到相关关系" in m for m in info_msgs)


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
