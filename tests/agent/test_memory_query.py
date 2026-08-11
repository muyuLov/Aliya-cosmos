"""测试 MemoryQueryTool：双路径查询 / 参数防御 / 降级行为"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.context import AgentContext
from agent.config import AgentConfig
from agent.tools.memory_query import MemoryQueryTool


def _make_ctx(**overrides) -> AgentContext:
    kwargs: dict = {
        "conv": MagicMock(),
        "registry": MagicMock(),
        "config": AgentConfig(),
        "prompt_manager": MagicMock(),
        "brain": MagicMock(),
        "emotion": MagicMock(),
        "cognition": None,
    }
    kwargs.update(overrides)
    return AgentContext(**kwargs)


def _make_memory_manager(
    answer: str | None = None,
    quintuples: list | None = None,
    query_error: Exception | None = None,
):
    mm = MagicMock()
    mm.query_memory = AsyncMock(side_effect=query_error) if query_error else AsyncMock(return_value=answer)
    mm.get_relevant_memories = AsyncMock(return_value=quintuples or [])
    return mm


@pytest.mark.asyncio
async def test_empty_query_rejected():
    ctx = _make_ctx(memory_manager=_make_memory_manager())
    result = await MemoryQueryTool().execute({"query": "   "}, ctx)
    assert result.success is False
    assert result.error is not None
    assert "不能为空" in result.error


@pytest.mark.asyncio
async def test_memory_manager_unavailable():
    ctx = _make_ctx(memory_manager=None)
    result = await MemoryQueryTool().execute({"query": "你好"}, ctx)
    assert result.success is False
    assert result.error is not None
    assert "不可用" in result.error


@pytest.mark.asyncio
async def test_combined_answer_and_memories():
    mm = _make_memory_manager(
        answer="你喜欢蓝色。",
        quintuples=[("Aliya", "Entity", "知道", "蓝色", "偏好")],
    )
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "颜色"}, ctx)
    assert result.success is True
    text = result.data["result"]
    assert "回答：你喜欢蓝色。" in text
    assert "相关记忆：" in text
    assert "Aliya(Entity) —[知道]-> 蓝色(偏好)" in text


@pytest.mark.asyncio
async def test_answer_only():
    mm = _make_memory_manager(answer="没有查到相关记忆。", quintuples=[])
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "xxx"}, ctx)
    assert result.success is True
    assert "回答：" in result.data["result"]
    assert "相关记忆：" not in result.data["result"]


@pytest.mark.asyncio
async def test_memories_only_when_answer_none():
    mm = _make_memory_manager(
        answer=None,
        quintuples=[("Aliya", "Entity", "喜欢", "音乐", "偏好")],
    )
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "爱好"}, ctx)
    assert result.success is True
    assert "相关记忆：" in result.data["result"]
    assert "回答：" not in result.data["result"]


@pytest.mark.asyncio
async def test_no_result():
    mm = _make_memory_manager(answer=None, quintuples=[])
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "未知"}, ctx)
    assert result.success is True
    assert result.data["result"] == "未找到相关记忆"


@pytest.mark.asyncio
async def test_limit_passed_and_clamped():
    mm = _make_memory_manager(quintuples=[("a", "E", "r", "b", "T")] * 5)
    ctx = _make_ctx(memory_manager=mm)
    await MemoryQueryTool().execute({"query": "q", "limit": 99}, ctx)
    mm.get_relevant_memories.assert_awaited_once_with("q", limit=20)  # 上限截断


@pytest.mark.asyncio
async def test_rag_failure_falls_back_to_memories():
    mm = _make_memory_manager(
        query_error=RuntimeError("llm down"),
        quintuples=[("Aliya", "Entity", "记得", "约定", "约定")],
    )
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "约定"}, ctx)
    assert result.success is True
    assert "相关记忆：" in result.data["result"]


@pytest.mark.asyncio
async def test_memory_manager_without_extra_capabilities():
    """仅暴露 query_memory 的降级实现也能工作（能力缺失降级）。"""
    mm = MagicMock()
    mm.query_memory = AsyncMock(return_value="回答")
    # 不含 get_relevant_memories 属性
    ctx = _make_ctx(memory_manager=mm)
    result = await MemoryQueryTool().execute({"query": "q"}, ctx)
    assert result.success is True
    assert "回答：回答" in result.data["result"]


def test_format_quintuple():
    tool = MemoryQueryTool()
    assert tool._format_quintuple(("Aliya", "Entity", "知道", "蓝色", "偏好")) == (
        "Aliya(Entity) —[知道]-> 蓝色(偏好)"
    )


def test_format_quintuple_malformed():
    tool = MemoryQueryTool()
    assert tool._format_quintuple(("短",)) == "('短',)"  # 异常回退为原样
