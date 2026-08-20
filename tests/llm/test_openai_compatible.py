"""测试 OpenAICompatibleProvider 的原生 function calling 透传与解析"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.llm.models import ChatRequest
from core.llm.providers.openai_compatible import OpenAICompatibleProvider

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_query",
            "description": "查询记忆",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


@pytest.fixture
def provider():
    return OpenAICompatibleProvider({"url": "http://localhost:9999", "model": "test-model", "api_key": "x"})


def _mock_response(*, content: str = "", tool_calls=None, finish_reason: str = "stop") -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content="")
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=None,
    )


@pytest.mark.asyncio
async def test_build_kwargs_without_tools(provider):
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}], model="test-model")
    kwargs = provider._build_kwargs(req)
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


@pytest.mark.asyncio
async def test_build_kwargs_with_tools(provider):
    req = ChatRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        tools=TOOLS,
        tool_choice="auto",
    )
    kwargs = provider._build_kwargs(req)
    assert kwargs["tools"] == TOOLS
    assert kwargs["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_parse_tool_calls_from_response(provider):
    tool_call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="memory_query", arguments='{"query": "昨天"}'),
    )
    create = AsyncMock(return_value=_mock_response(tool_calls=[tool_call], finish_reason="tool_calls"))
    provider._async_client.chat.completions.create = create

    resp = await provider.async_chat_completion(
        ChatRequest(messages=[{"role": "user", "content": "hi"}], model="test-model", tools=TOOLS)
    )
    assert resp.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "memory_query", "arguments": '{"query": "昨天"}'},
        }
    ]
    assert resp.finish_reason == "tool_calls"
    # 透传校验：create 收到的 kwargs 含 tools
    call = create.await_args
    assert call is not None
    assert call.kwargs["tools"] == TOOLS


@pytest.mark.asyncio
async def test_no_tool_calls_when_absent(provider):
    create = AsyncMock(return_value=_mock_response(content="普通回复"))
    provider._async_client.chat.completions.create = create
    resp = await provider.async_chat_completion(
        ChatRequest(messages=[{"role": "user", "content": "hi"}], model="test-model")
    )
    assert resp.tool_calls is None
    assert resp.content == "普通回复"
