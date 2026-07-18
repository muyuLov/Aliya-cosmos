"""测试 ConversationService：消息历史管理、重试、上下文注入、消息清理"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llm.cache import ContextCache
from core.llm.exceptions import LLMRequestError
from core.llm.models import ChatResponse, TokenUsage
from core.llm.service import ConversationService


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.model = "test-model"
    provider.provider_name = "test"
    provider.async_chat_completion = AsyncMock(
        return_value=ChatResponse(
            content="你好！",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
    )
    provider.stream_chat_completion = AsyncMock()
    provider.stream_chat_completion.return_value.__aiter__.return_value = iter(["你", "好", "！"])
    provider.aclose = AsyncMock()
    return provider


@pytest.fixture
def service(mock_provider):
    return ConversationService(
        provider=mock_provider,
        cache=ContextCache(ttl=3600),
        conversation_id="test-session",
        system_prompt="你是助手",
    )


class TestConversationService:
    @pytest.mark.asyncio
    async def test_asend_returns_reply(self, service):
        reply = await service.asend("你好")
        assert reply == "你好！"

    @pytest.mark.asyncio
    async def test_asend_stores_history(self, service):
        await service.asend("你好")
        history = await service.get_history()
        assert len(history) == 2  # user + assistant
        assert history[0].role == "user"
        assert history[0].content == "你好"
        assert history[1].role == "assistant"
        assert history[1].content == "你好！"

    @pytest.mark.asyncio
    async def test_asend_no_history(self, service, mock_provider):
        """store_history=False 时不写入用户消息"""
        await service.asend("你好", store_history=False)
        history = await service.get_history()
        assert len(history) == 1  # 仅 assistant 回复，没有 user 消息
        assert history[0].role == "assistant"

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self, service, mock_provider):
        await service.asend("hi")
        # 验证 provider 收到的消息包含 system prompt
        call_kwargs = mock_provider.async_chat_completion.call_args
        request = call_kwargs[0][0]
        assert request.messages[0] == {"role": "system", "content": "你是助手"}

    @pytest.mark.asyncio
    async def test_get_history_returns_copy(self, service):
        await service.asend("hi")
        h1 = await service.get_history()
        h2 = await service.get_history()
        assert len(h1) == len(h2)
        # 修改副本不影响原始
        h1.clear()
        h3 = await service.get_history()
        assert len(h3) > 0

    @pytest.mark.asyncio
    async def test_clear_history(self, service):
        await service.asend("msg1")
        assert len(await service.get_history()) == 2
        await service.clear_history()
        assert await service.get_history() == []

    @pytest.mark.asyncio
    async def test_clear_history_preserves_system_prompt(self, service):
        await service.asend("hi")
        await service.clear_history()
        # system_prompt 应保留
        assert service._context.system_prompt == "你是助手"

    @pytest.mark.asyncio
    async def test_set_system_prompt(self, service):
        await service.set_system_prompt("新系统提示")
        assert service._context.system_prompt == "新系统提示"

    @pytest.mark.asyncio
    async def test_set_context_injection(self, service, mock_provider):
        await service.set_context_injection(tools="工具描述", memory="记忆内容")
        await service.asend("hi")
        call_kwargs = mock_provider.async_chat_completion.call_args
        request = call_kwargs[0][0]
        # system 消息应包含 injection 内容
        sys_content = request.messages[0]["content"]
        assert "Available Tools" in sys_content
        assert "Relevant Memory" in sys_content
        assert "工具描述" in sys_content
        assert "记忆内容" in sys_content

    @pytest.mark.asyncio
    async def test_context_injection_cleared_after_call(self, service):
        await service.set_context_injection(tools="desc")
        await service.asend("hi")
        # 下一轮不应包含 injection
        assert service._context_injection == ""

    @pytest.mark.asyncio
    async def test_append_message(self, service):
        await service.append_message("assistant", "测试消息", metadata={"injected": True})
        history = await service.get_history()
        assert len(history) == 1
        assert history[0].content == "测试消息"
        assert history[0].metadata == {"injected": True}

    @pytest.mark.asyncio
    async def test_discard_messages(self, service):
        await service.append_message(
            "assistant", "工具结果", metadata={"injected": True, "prefix": "tool_result"}
        )
        await service.append_message("user", "正常消息")
        await service.discard_messages("tool_result", max_count=5)
        history = await service.get_history()
        assert len(history) == 1
        assert history[0].role == "user"

    @pytest.mark.asyncio
    async def test_discard_messages_max_count(self, service):
        for i in range(5):
            await service.append_message(
                "assistant", f"工具结果{i}", metadata={"injected": True, "prefix": "tool_result"}
            )
        await service.discard_messages("tool_result", max_count=2)
        history = await service.get_history()
        # 最多只删 2 条
        injected_count = sum(
            1 for m in history if m.metadata and m.metadata.get("prefix") == "tool_result"
        )
        assert injected_count == 3

    @pytest.mark.asyncio
    async def test_discard_messages_zero_max_count(self, service):
        await service.append_message(
            "assistant", "tool result", metadata={"injected": True, "prefix": "tool_result"}
        )
        await service.discard_messages("tool_result", max_count=0)
        assert len(await service.get_history()) == 1

    @pytest.mark.asyncio
    async def test_retry_on_llm_error_then_success(self, mock_provider, service):
        call_count = 0

        async def flaky_chat(_request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMRequestError(provider="test", reason="timeout")
            return ChatResponse(content="重试成功", usage=TokenUsage())

        mock_provider.async_chat_completion = flaky_chat
        reply = await service.asend("test", max_retries=3)
        assert reply == "重试成功"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_fail_raises(self, mock_provider, service):
        mock_provider.async_chat_completion.side_effect = LLMRequestError(
            provider="test", reason="always fail"
        )
        with pytest.raises(LLMRequestError, match="always fail"):
            await service.asend("test", max_retries=2)

    @pytest.mark.asyncio
    async def test_rollback_on_error(self, service, mock_provider):
        """失败后用户消息应被回滚"""
        mock_provider.async_chat_completion.side_effect = LLMRequestError(
            provider="test", reason="fail"
        )
        with pytest.raises(LLMRequestError):
            await service.asend("这条消息应被回滚", max_retries=1)
        history = await service.get_history()
        assert len(history) == 0  # 用户消息已回滚，没有 assistant 回复

    @pytest.mark.asyncio
    async def test_usage_tracking(self, mock_provider, service):
        await service.asend("msg1")
        await service.asend("msg2")
        usage = await service.get_usage()
        assert usage.prompt_tokens == 20
        assert usage.completion_tokens == 10
        assert usage.total_tokens == 30

    @pytest.mark.asyncio
    async def test_reset_usage(self, service):
        await service.asend("hi")
        await service.reset_usage()
        usage = await service.get_usage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0

    @pytest.mark.asyncio
    async def test_aclose(self, mock_provider, service):
        await service.aclose()
        mock_provider.aclose.assert_awaited_once()
        # 幂等：再次调用不应抛异常
        await service.aclose()

    @pytest.mark.asyncio
    async def test_async_context_manager(self, mock_provider):
        async with ConversationService(
            provider=mock_provider,
            cache=ContextCache(ttl=3600),
        ) as svc:
            reply = await svc.asend("hi")
            assert reply
        mock_provider.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_emotion_patch(self, service, mock_provider):
        await service.set_emotion_patch("（开心地）")
        await service.asend("hi")
        call = mock_provider.async_chat_completion.call_args
        sys_content = call[0][0].messages[0]["content"]
        assert "（开心地）" in sys_content

    @pytest.mark.asyncio
    async def test_conversation_id_generation(self):
        from core.llm.cache import ContextCache
        from unittest.mock import MagicMock

        svc = ConversationService(
            provider=MagicMock(),
            cache=ContextCache(ttl=3600),
        )
        assert svc.conversation_id is not None
        assert len(svc.conversation_id) > 0
