"""测试 LLM 数据模型：Message、ConversationContext、TokenUsage、ChatRequest、ChatResponse"""

from __future__ import annotations

from core.llm.models import (
    ChatRequest,
    ChatResponse,
    ConversationContext,
    Message,
    TokenUsage,
)


class TestMessage:
    def test_create_user_message(self):
        msg = Message(role="user", content="你好")
        assert msg.role == "user"
        assert msg.content == "你好"
        assert msg.metadata is None

    def test_create_assistant_message(self):
        msg = Message(role="assistant", content="你好！")
        assert msg.role == "assistant"

    def test_to_api_dict(self):
        msg = Message(role="user", content="test")
        assert msg.to_api_dict() == {"role": "user", "content": "test"}

    def test_api_dict_excludes_metadata(self):
        msg = Message(role="assistant", content="reply", metadata={"injected": True})
        d = msg.to_api_dict()
        assert "metadata" not in d
        assert d == {"role": "assistant", "content": "reply"}

    def test_frozen_immutable(self):
        msg = Message(role="user", content="original")
        import pytest
        with pytest.raises(Exception):  # frozen=True 阻止修改
            msg.content = "modified"

    def test_with_metadata(self):
        msg = Message(role="assistant", content="result", metadata={"prefix": "tool"})
        assert msg.metadata == {"prefix": "tool"}


class TestConversationContext:
    def test_default_fields(self):
        ctx = ConversationContext(conversation_id="abc-123")
        assert ctx.conversation_id == "abc-123"
        assert ctx.system_prompt is None
        assert ctx.messages == []
        assert ctx.created_at > 0
        assert ctx.updated_at > 0

    def test_with_messages(self):
        msg = Message(role="user", content="hi")
        ctx = ConversationContext(conversation_id="x", messages=[msg])
        assert len(ctx.messages) == 1
        assert ctx.messages[0].content == "hi"

    def test_with_system_prompt(self):
        ctx = ConversationContext(conversation_id="x", system_prompt="你是一个助手")
        assert ctx.system_prompt == "你是一个助手"


class TestTokenUsage:
    def test_default_all_zero(self):
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0
        assert u.reasoning_tokens == 0

    def test_add_usage(self):
        a = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        b = TokenUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15)
        c = a + b
        assert c.prompt_tokens == 15
        assert c.completion_tokens == 30
        assert c.total_tokens == 45

    def test_add_preserves_deepseek_fields(self):
        a = TokenUsage(prompt_cache_hit_tokens=100, reasoning_tokens=50)
        b = TokenUsage(prompt_cache_miss_tokens=200)
        c = a + b
        assert c.prompt_cache_hit_tokens == 100
        assert c.prompt_cache_miss_tokens == 200
        assert c.reasoning_tokens == 50

    def test_add_does_not_mutate_originals(self):
        a = TokenUsage(prompt_tokens=10)
        b = TokenUsage(prompt_tokens=20)
        c = a + b
        assert a.prompt_tokens == 10
        assert b.prompt_tokens == 20
        assert c.prompt_tokens == 30


class TestChatRequest:
    def test_minimal_request(self):
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], model="gpt-4")
        assert req.model == "gpt-4"
        assert len(req.messages) == 1
        assert req.temperature is None
        assert req.max_tokens is None

    def test_with_optional_fields(self):
        req = ChatRequest(
            messages=[], model="deepseek", temperature=0.7, max_tokens=100,
        )
        assert req.temperature == 0.7
        assert req.max_tokens == 100

    def test_extra_defaults_empty(self):
        req = ChatRequest(messages=[], model="m")
        assert req.extra == {}

    def test_thinking_default_none(self):
        req = ChatRequest(messages=[], model="m")
        assert req.thinking is None
        assert req.reasoning_effort is None

    def test_thinking_enabled(self):
        req = ChatRequest(messages=[], model="deepseek",
                          thinking={"type": "enabled"}, reasoning_effort="high")
        assert req.thinking == {"type": "enabled"}
        assert req.reasoning_effort == "high"


class TestMessageReasoning:
    """测试 Message 的 reasoning_content 与 to_full_api_dict"""

    def test_reasoning_content_default(self):
        msg = Message(role="assistant", content="reply")
        assert msg.reasoning_content == ""

    def test_to_full_api_dict_without_reasoning(self):
        msg = Message(role="assistant", content="reply")
        d = msg.to_full_api_dict()
        assert d == {"role": "assistant", "content": "reply"}
        assert "reasoning_content" not in d

    def test_to_full_api_dict_with_reasoning(self):
        msg = Message(role="assistant", content="最终回复", reasoning_content="逐步推理...")
        d = msg.to_full_api_dict()
        assert d["role"] == "assistant"
        assert d["content"] == "最终回复"
        assert d["reasoning_content"] == "逐步推理..."


class TestChatResponse:
    def test_minimal_response(self):
        resp = ChatResponse(content="hello")
        assert resp.content == "hello"
        assert resp.finish_reason == "stop"
        assert resp.usage.prompt_tokens == 0
        assert resp.raw_response is None

    def test_with_usage(self):
        usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        resp = ChatResponse(content="reply", finish_reason="length", usage=usage)
        assert resp.finish_reason == "length"
        assert resp.usage.total_tokens == 15
        assert resp.usage.prompt_tokens == 10

    def test_reasoning_content_default(self):
        resp = ChatResponse(content="reply")
        assert resp.reasoning_content == ""

    def test_reasoning_content_explicit(self):
        resp = ChatResponse(content="最终回复", reasoning_content="逐步推理过程")
        assert resp.reasoning_content == "逐步推理过程"
        assert resp.content == "最终回复"
