"""端到端集成测试：真实 AgentLoop + ConversationService + mock LLM provider + WS 网关

覆盖完整链路：user_message → TOOL_PHASE（工具调用/执行/结果）→ SOUL_PHASE（流式回复）→ run_finished。
"""

from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.context import ContextBuilder
from agent.loop import AgentLoop
from agent.session import AgentSession
from agent.tools import PermissionChecker, ToolRegistry
from agent.tools.builtin import register_builtin_tools
from agent.ws import create_ws_router
from core.llm.cache import ContextCache
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService

PROMPTS_DIR = "data/prompts"

TOOL_CALL = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "memory_query", "arguments": '{"query": "昨天聊了什么"}'},
    }
]


def build_provider(tool_rounds: int = 1):
    """真实 LLM 调用序列：
    前 tool_rounds 次 async_chat_completion → 返回 tool_calls（触发工具执行）
    随后 → 返回普通文本（结束工具阶段）
    stream_chat_completion → 逐 token 流式回复
    """
    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.supports_thinking = True
    provider.last_stream_usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    effects = [ChatResponse(content="", finish_reason="tool_calls", tool_calls=TOOL_CALL)] * tool_rounds
    effects.append(
        ChatResponse(
            content="无需更多工具",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
    )
    provider.async_chat_completion = AsyncMock(side_effect=effects)
    tokens = ["你", "好", "！"]

    async def fake_stream(_request):
        for tok in tokens:
            yield tok

    provider.stream_chat_completion = fake_stream
    provider.aclose = AsyncMock()
    return provider


def build_session_factory(tool_rounds: int = 1):
    """生产式装配：真实 AgentLoop（mock LLM）+ 内置工具 + 全允许权限。"""
    tmp = pathlib.Path(tempfile.mkdtemp()) / "Permissions.yml"
    tmp.write_text("tools:\n  memory_query: always_allow\n", encoding="utf-8")
    checker = PermissionChecker(str(tmp))

    async def factory(conversation_id: str) -> AgentSession:
        provider = build_provider(tool_rounds=tool_rounds)
        service = ConversationService(
            provider=provider,
            cache=ContextCache(ttl=3600),
            conversation_id=conversation_id,
        )
        registry = ToolRegistry()
        register_builtin_tools(registry)
        loop = AgentLoop(
            service=service,
            registry=registry,
            checker=checker,
            context=ContextBuilder(PROMPTS_DIR),
        )
        return AgentSession(conversation_id, service, loop)

    return factory


@pytest.fixture
def client():
    app = FastAPI(title="Integration Test")
    app.include_router(create_ws_router(session_factory=build_session_factory()))
    return TestClient(app)


class TestEndToEnd:
    def test_full_flow_with_tool_and_stream(self, client):
        with client.websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "user_message", "text": "查一下昨天聊了什么"})
            received = []
            while True:
                d = ws.receive_json()
                received.append(d)
                if d["type"] == "token_usage":
                    break

        types = [d["type"] for d in received]
        # 回合开始
        assert types[0] == "run_started"
        # 工具阶段完整序列
        assert "step_started" in types
        assert "tool_call_start" in types
        tool_start = next(d for d in received if d["type"] == "tool_call_start")
        assert tool_start["tool_name"] == "memory_query"
        assert "tool_call_result" in types
        result = next(d for d in received if d["type"] == "tool_call_result")
        # 记忆不可用时的降级文案（本测试未注入 memory）
        assert result["output"]
        assert "tool_call_end" in types
        # 灵魂阶段流式
        assert types.count("text_message_content") >= 1
        text_end = next(d for d in received if d["type"] == "text_message_end")
        assert text_end["full_text"] == "你好！"
        # 回合结束
        assert "run_finished" in types
        assert "token_usage" in types

    def test_no_tool_when_plain_reply(self):
        """模型不请求工具时直接进入流式回复。"""
        app = FastAPI(title="Integration Test")
        app.include_router(create_ws_router(session_factory=build_session_factory(tool_rounds=0)))
        with TestClient(app).websocket_connect("/agent/ws") as ws:
            ws.send_json({"type": "user_message", "text": "你好"})
            received = []
            while True:
                d = ws.receive_json()
                received.append(d)
                if d["type"] == "token_usage":
                    break
        types = [d["type"] for d in received]
        assert "tool_call_start" not in types
        assert "text_message_end" in types
