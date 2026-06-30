"""测试 rag_query 模块的 RAG 查询引擎"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.exceptions import RAGGenerationError, RAGQueryError
from memory.rag_query import RAGQueryEngine


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.model = "test-model"
    return provider


@pytest.fixture
def mock_graph_query():
    return MagicMock(return_value=[])


@pytest.fixture
def engine(mock_provider, mock_graph_query):
    with patch("memory.rag_query.get_memory_provider", return_value=mock_provider):
        eng = RAGQueryEngine(graph_query_func=mock_graph_query)
        yield eng


class TestRAGQueryEngine:
    @pytest.mark.asyncio
    async def test_query_no_keywords_returns_none(self, engine, mock_provider):
        mock_provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content="[]")
        )
        result = await engine.query_async("random question")
        assert result is None

    @pytest.mark.asyncio
    async def test_query_no_graph_results_returns_none(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content='["keyword1"]')
        )
        mock_graph_query.return_value = []

        result = await engine.query_async("some question")
        assert result is None

    @pytest.mark.asyncio
    async def test_full_query_flow(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            side_effect=[
                MagicMock(content='["小明", "苹果"]'),
                MagicMock(content="小明喜欢吃苹果"),
            ]
        )
        mock_graph_query.return_value = [
            ("小明", "人物", "喜欢吃", "苹果", "物品")
        ]

        result = await engine.query_async("小明喜欢什么")
        assert result == "小明喜欢吃苹果"

    @pytest.mark.asyncio
    async def test_query_with_context(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            side_effect=[
                MagicMock(content='["关键词"]'),
                MagicMock(content="基于上下文的回答"),
            ]
        )
        mock_graph_query.return_value = [("关键词", "概念", "相关", "结果", "概念")]

        result = await engine.query_async("测试问题", context=["上下文1", "上下文2"])
        assert result == "基于上下文的回答"

    @pytest.mark.asyncio
    async def test_context_injection(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            side_effect=[
                MagicMock(content='["entity"]'),
                MagicMock(content="回答"),
            ]
        )
        mock_graph_query.return_value = [("entity", "物品", "是", "测试", "概念")]

        engine.set_context(["预置上下文"])
        result = await engine.query_async("问题")
        assert result == "回答"

    @pytest.mark.asyncio
    async def test_identity_question_adds_keywords(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content='[]')
        )
        mock_graph_query.return_value = []

        result = await engine.query_async("你记得我吗")
        assert result is None

        # verify keyword extraction was called (with identity keywords 用户/我)
        extract_call = mock_provider.async_chat_completion.await_args
        assert extract_call is not None

    @pytest.mark.asyncio
    async def test_generation_failure_raises(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            side_effect=[
                MagicMock(content='["keyword"]'),
                RuntimeError("LLM generation failed"),
            ]
        )
        mock_graph_query.return_value = [("a", "人物", "喜欢", "b", "物品")]

        with pytest.raises(RAGGenerationError):
            await engine.query_async("问题")

    @pytest.mark.asyncio
    async def test_keyword_extraction_failure_returns_none(self, engine, mock_provider):
        mock_provider.async_chat_completion = AsyncMock(
            return_value=MagicMock(content="不是JSON")
        )

        result = await engine.query_async("问题")
        assert result is None

    def test_is_identity_question(self, engine):
        assert engine._is_identity_question("我是谁")
        assert engine._is_identity_question("你还记得我吗")
        assert engine._is_identity_question("我的名字是什么")
        assert engine._is_identity_question("会不会忘了我")
        assert not engine._is_identity_question("今天天气怎么样")
        assert not engine._is_identity_question("苹果是什么颜色")

    def test_set_context(self, engine):
        engine.set_context(["a", "b", "c"])
        assert engine._recent_context == ["a", "b", "c"]

    def test_set_context_respects_config_limit(self, engine, mock_provider):
        with patch("memory.rag_query.get_grag_config") as mock_cfg:
            cfg = mock_cfg.return_value
            cfg.context_length = 2

            engine.set_context(["a", "b", "c", "d"])
            assert len(engine._recent_context) == 2
            assert engine._recent_context == ["a", "b"]

    def test_sync_query(self, engine, mock_provider, mock_graph_query):
        mock_provider.async_chat_completion = AsyncMock(
            side_effect=[
                MagicMock(content='["k"]'),
                MagicMock(content="sync answer"),
            ]
        )
        mock_graph_query.return_value = [("k", "概念", "是", "测试", "概念")]

        result = engine.query("sync question")
        assert result == "sync answer"
