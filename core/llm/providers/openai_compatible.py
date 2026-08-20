"""OpenAI 兼容接口提供商：统一的 AsyncOpenAI 客户端，通过标准配置驱动所有兼容 API"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import httpx
from openai import AsyncOpenAI

from core.llm.exceptions import LLMRequestError
from core.llm.models import ChatRequest, ChatResponse
from core.llm.providers.base import OPENAI_COMMON_EXCEPTIONS, LLMProvider, extract_openai_usage
from core.logger import get_logger

_logger = get_logger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI 兼容接口提供商，适用于所有标准 OpenAI API 格式的服务。

    配置字段：
        url:        API 基础地址（如 ``https://api.deepseek.com`` 或 ``http://localhost:11434``）
        api_key:    API 密钥（本地服务可为占位符）
        model:      模型名称
        timeout:    请求超时秒数（可选，默认继承自 LLMProvider）
        max_retries: 最大重试次数（可选，默认继承自 LLMProvider）
        http2:      是否启用 HTTP/2（可选，默认 True，LM Studio 需设为 False）

    支持 DeepSeek 思考模式（thinking mode）：当 ``request.thinking`` 不为 None 时，
    自动提取 API 响应的 ``reasoning_content`` 字段并回填到 ``ChatResponse`` 中。
    """

    # 与 _build_kwargs 显式参数冲突的 extra 字段，调用时自动过滤
    _RESERVED_EXTRA_KEYS: frozenset[str] = frozenset({
        "model", "messages", "temperature", "max_tokens",
        "stream", "stream_options", "thinking", "reasoning_effort",
        "tools", "tool_choice",
    })

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        base_url = str(config.get("url", "")).rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        api_key = str(config.get("api_key") or config.get("key") or "not-needed")
        http_client: httpx.AsyncClient | None = None
        if config.get("http2") is False:
            http_client = httpx.AsyncClient(http2=False)

        self._async_client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(config.get("timeout", self.timeout)),
            max_retries=config.get("max_retries", self.max_retries),
            http_client=http_client,
        )

    @property
    def provider_name(self) -> str:
        """提供商名称，用于日志和错误信息标识。"""
        return str(self.config.get("provider_name", "openai_compatible"))

    @property
    def supports_thinking(self) -> bool:
        """当前模型是否支持思考模式。基于模型名称检测。"""
        return "deepseek" in self.model.lower()

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

    @classmethod
    def _filter_extra(cls, extra: dict[str, Any]) -> dict[str, Any]:
        """过滤掉与显式参数冲突的 extra 字段。"""
        if not extra:
            return {}
        return {k: v for k, v in extra.items() if k not in cls._RESERVED_EXTRA_KEYS}

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
        # 原生 function calling：tools / tool_choice 直接透传给 API
        if request.tools:
            kwargs["tools"] = request.tools
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
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
            tool_calls = None
            raw_tool_calls = getattr(choice.message, "tool_calls", None)
            if raw_tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in raw_tool_calls
                ]
            self._log_response(choice.finish_reason or "stop", len(choice.message.content or ""), usage)
            return ChatResponse(
                content=choice.message.content or "",
                reasoning_content=str(reasoning_raw),
                finish_reason=choice.finish_reason or "stop",
                usage=usage,
                raw_response=response,
                tool_calls=tool_calls,
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
