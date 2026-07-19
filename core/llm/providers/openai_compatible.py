"""OpenAI 兼容接口提供商基类：封装 async_chat_completion 与 stream_chat_completion 公共逻辑"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from core.llm.exceptions import LLMRequestError
from core.llm.models import ChatRequest, ChatResponse
from core.llm.providers.base import OPENAI_COMMON_EXCEPTIONS, LLMProvider, extract_openai_usage
from core.logger import get_logger

_logger = get_logger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI 兼容接口提供商基类。

    封装所有基于 AsyncOpenAI 的提供商（DeepSeek、LM Studio 等）共用的
    async_chat_completion 与 stream_chat_completion 实现，子类只需提供
    _build_client() 和 provider_name。

    支持 DeepSeek 思考模式（thinking mode）：当 ``request.thinking`` 不为 None 时，
    自动提取 API 响应的 ``reasoning_content`` 字段并回填到 ``ChatResponse`` 中。

    Args:
        config: 提供商配置字典，至少包含 model 字段。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self._async_client: AsyncOpenAI = self._build_client(config)

    @property
    def supports_thinking(self) -> bool:
        """当前模型是否支持思考模式（thinking mode）。

        默认检测逻辑：提供商名或模型名含 "deepseek" 则认为支持。
        子类可覆盖此属性实现更精确的检测。
        """
        name = (self.provider_name + " " + self.model).lower()
        return "deepseek" in name

    @abstractmethod
    def _build_client(self, config: dict[str, Any]) -> AsyncOpenAI:
        """构造并返回 AsyncOpenAI 客户端，由子类实现以注入不同的 base_url / http_client。"""

    def _log_request(self, request: ChatRequest, *, stream: bool = False) -> None:
        """记录请求日志。"""
        _logger.debug(
            "发送%s对话请求 | provider=%s | model=%s | messages=%d | thinking=%s",
            "流式" if stream else "",
            self.provider_name,
            request.model or self.model,
            len(request.messages),
            request.thinking,
        )

    def _log_response(self, finish_reason: str, content_len: int, usage: Any, *, stream: bool = False) -> None:
        """记录响应日志。"""
        _logger.debug(
            "收到%s对话响应 | finish_reason=%s | length=%d"
            " | prompt=%d | completion=%d | total=%d"
            " | cache_hit=%d | cache_miss=%d | reasoning=%d",
            "流式" if stream else "",
            finish_reason,
            content_len,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            usage.prompt_cache_hit_tokens,
            usage.prompt_cache_miss_tokens,
            usage.reasoning_tokens,
        )

    @staticmethod
    def _filter_extra(extra: dict[str, Any]) -> dict[str, Any]:
        """过滤掉与显式参数冲突的 extra 字段。"""
        RESERVED = {
            "model", "messages", "temperature", "max_tokens",
            "stream", "stream_options", "thinking", "reasoning_effort",
        }
        return {k: v for k, v in extra.items() if k not in RESERVED}

    def _build_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        """构建 API 调用的公共关键字参数。"""
        kwargs: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": request.messages,
            "temperature": self._or_not_given(request.temperature),
            "max_tokens": self._or_not_given(request.max_tokens),
            **self._filter_extra(request.extra),
        }
        # DeepSeek 思考模式：thinking 须放在 extra_body 中传递
        if request.thinking is not None and isinstance(request.thinking, dict):
            kwargs.setdefault("extra_body", {}).update(thinking=request.thinking)
        if request.reasoning_effort is not None:
            kwargs["reasoning_effort"] = request.reasoning_effort
        return kwargs

    async def async_chat_completion(self, request: ChatRequest) -> ChatResponse:
        """原生异步对话补全，直接调用 AsyncOpenAI，不阻塞事件循环。"""
        self._log_request(request)
        try:
            kwargs = self._build_kwargs(request)
            kwargs["stream"] = False
            response = await self._async_client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
            if not response.choices:
                raise LLMRequestError(
                    provider=self.provider_name,
                    reason="API 返回空 choices，对话未产生有效回复",
                )
            choice = response.choices[0]
            usage = extract_openai_usage(response.usage)
            reasoning_raw = getattr(choice.message, "reasoning_content", None) or ""
            self._log_response(choice.finish_reason or "stop", len(choice.message.content or ""), usage)
            return ChatResponse(
                content=choice.message.content or "",
                reasoning_content=str(reasoning_raw),
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
                raw_response=response,
            )
        except OPENAI_COMMON_EXCEPTIONS as exc:
            raise LLMRequestError(
                provider=self.provider_name,
                reason=str(exc),
                cause=exc,
            ) from exc

    async def stream_chat_completion(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """
        原生异步流式对话补全，直接 ``async for`` 消费 SSE 流。

        流结束后通过 ``stream.get_final_usage()`` 获取 token 用量并记录日志。

        Yields:
            文本片段（token）。

        Raises:
            LLMRequestError: 请求失败时抛出。
        """
        self._log_request(request, stream=True)
        try:
            # 保存最后一次非空的 reasoning_content 碎片，流结束后可获取完整内容
            last_reasoning_chunk = ""
            kwargs = self._build_kwargs(request)
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            stream = await self._async_client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
            content_len = 0
            final_usage = None
            async for chunk in stream:
                # include_usage=True 时，最后一个 chunk choices 为空但携带 usage
                if chunk.usage is not None:
                    final_usage = chunk.usage
                if not chunk.choices:
                    continue
                token = chunk.choices[0].delta.content or ""
                if token:
                    content_len += len(token)
                    yield token
                # 收集 reasoning_content（流式场景下的思考内容）
                reasoning_delta = getattr(chunk.choices[0].delta, "reasoning_content", None) or ""
                if reasoning_delta:
                    last_reasoning_chunk += reasoning_delta
            usage = extract_openai_usage(final_usage)
            self.last_stream_usage = usage
            self._log_response("stop", content_len, usage, stream=True)
            if last_reasoning_chunk:
                _logger.debug("[Stream] reasoning_content 收集完成 | len=%d", len(last_reasoning_chunk))
        except OPENAI_COMMON_EXCEPTIONS as exc:
            raise LLMRequestError(
                provider=self.provider_name,
                reason=str(exc),
                cause=exc,
            ) from exc

    async def aclose(self) -> None:
        """关闭 AsyncOpenAI 客户端，释放底层 httpx 连接池资源。"""
        await self._async_client.close()
