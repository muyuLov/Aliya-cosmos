"""LLM 数据模型：消息、对话上下文、请求与响应的 Pydantic 结构定义"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    """单条对话消息，不可变（frozen=True）。"""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str
    reasoning_content: str = ""
    metadata: dict[str, Any] | None = None

    def to_api_dict(self) -> dict[str, str]:
        """返回仅含 role 与 content 的字典，用于构造 API 请求体。"""
        return {"role": self.role, "content": self.content}

    def to_full_api_dict(self) -> dict[str, str]:
        """返回完整 API 字典，含 reasoning_content（如有）。

        根据 DeepSeek 思考模式规范：
        - 有工具调用时：必须回传 reasoning_content，否则 API 返回 400
        - 无工具调用时：可省略 reasoning_content
        """
        result: dict[str, str] = {"role": self.role, "content": self.content}
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        return result


class ConversationContext(BaseModel):
    """对话上下文快照，包含完整的消息历史与系统提示词。"""

    conversation_id: str
    system_prompt: str | None = None
    messages: list[Message] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ChatRequest(BaseModel):
    """提供商无关的对话请求结构。"""

    messages: list[dict[str, str]]
    model: str
    # None 表示使用提供商/模型的默认温度，不显式传递
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    # DeepSeek 思考模式参数：thinking={"type": "enabled"} 或 {"type": "disabled"}
    thinking: dict | None = None
    # 思考强度控制："high" / "max" / "low"
    reasoning_effort: str | None = None


class TokenUsage(BaseModel):
    """
    Token 用量统计，对应 OpenAI 兼容接口的 usage 字段。

    适用于所有提供商（Ollama、DeepSeek、LM Studio 等），
    DeepSeek 专有字段（prompt_cache_hit/miss_tokens、reasoning_tokens）
    在其他提供商中默认为 0。

    Attributes:
        prompt_tokens: 输入 token 总数。
        completion_tokens: 输出 token 总数（包含思维链 token）。
        total_tokens: 本次请求总 token 数（prompt + completion）。
        prompt_cache_hit_tokens: 命中 KV 缓存的 prompt token 数（DeepSeek 专有）。
        prompt_cache_miss_tokens: 未命中 KV 缓存的 prompt token 数（DeepSeek 专有）。
        reasoning_tokens: 思维链（CoT）token 数（DeepSeek 专有）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        p = self.prompt_tokens + other.prompt_tokens
        c = self.completion_tokens + other.completion_tokens
        t = self.total_tokens + other.total_tokens
        # 对齐 total_tokens：当累计值与 prompt+completion 之和偏差超过 1% 时校正
        expected = p + c
        if expected > 0 and t > 0 and abs(t - expected) / expected > 0.01:
            t = expected
        return TokenUsage(
            prompt_tokens=p,
            completion_tokens=c,
            total_tokens=t,
            prompt_cache_hit_tokens=self.prompt_cache_hit_tokens + other.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.prompt_cache_miss_tokens + other.prompt_cache_miss_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class ChatResponse(BaseModel):
    """统一的对话响应结构。

    Attributes:
        content: 模型生成的最终回复文本。
        reasoning_content: 思维链推理过程（DeepSeek 思考模式专有，
                           通过 reasoning_content 字段从 API 返回，
                           非思考模式或无思考内容的模型此字段为空）。
        finish_reason: 结束原因（"stop" / "length" / "tool_calls" 等）。
        usage: Token 用量统计。
        raw_response: 提供商原始响应对象，用于调试。
    """

    content: str
    reasoning_content: str = ""
    finish_reason: str = "stop"
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw_response: Any | None = None
