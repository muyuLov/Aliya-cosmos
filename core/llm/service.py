"""对话服务：对话编排（调用、重试、流式），上下文管理委托给 ConversationContextManager"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncGenerator, Literal

from core.llm.context_manager import ConversationContextManager
from core.llm.exceptions import LLMRequestError
from core.llm.models import ChatRequest, ChatResponse, ConversationContext, Message, TokenUsage
from core.llm.retry import async_llm_retry
from core.logger import get_logger

if TYPE_CHECKING:
    from core.llm.providers.base import LLMProvider

logger = get_logger(__name__)


class ConversationService:
    """
    对话服务，管理单个会话的完整生命周期。

    职责分工：
    - **ConversationService**（本类）：对话编排——send()/asend()/astream_send()
      的调用、重试、回滚、usage 统计、provider 资源管理与属性暴露。
    - **ConversationContextManager**（self._context_manager）：上下文管理——
      消息历史、系统提示词、一次性注入补丁、请求消息构建、上下文缓存持久化。

    **异步优先**：核心路径为 ``asend()`` / ``astream_send()``，
    均直接调用 ``provider.async_chat_completion()`` 或
    ``provider.stream_chat_completion()``，不阻塞事件循环。
    同步包装器 ``send()`` 通过 ``asyncio.run()`` 驱动异步路径，
    适用于脚本/REPL 等非异步上下文。

    **资源管理**：支持异步上下文管理器协议（``async with``），
    自动管理底层 LLM 提供商的资源生命周期（如 HTTP 连接、会话等）。

    Args:
        provider: LLM 提供商实例。
        history_max_chars: 触发自动清理的消息历史总字符数阈值，超限时移除最旧消息，默认 90000。
        conversation_id: 会话唯一标识符，为 None 时自动生成 UUID。
        system_prompt: 初始系统提示词，会覆盖缓存中的旧值。

    Examples:
        >>> from core.llm import create_from_config
        >>>
        >>> # 推荐：使用上下文管理器自动管理资源
        >>> async with create_from_config() as service:
        ...     reply = await service.asend("你好")
        >>>
        >>> # 同步调用（脚本/REPL）
        >>> service = create_from_config()
        >>> reply = service.send("你好")
        >>>
        >>> # 异步流式调用
        >>> async with create_from_config() as service:
        ...     async for token in service.astream_send("讲个故事"):
        ...         print(token, end="", flush=True)
    """

    def __init__(
        self,
        provider: LLMProvider,
        history_max_chars: int = 90000,
        conversation_id: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._provider = provider
        self._lock = asyncio.Lock()
        self._usage: TokenUsage = TokenUsage()
        self._closed: bool = False

        # 上轮响应的 reasoning_content（DeepSeek 思考模式专有）
        self._last_reasoning_content: str = ""

        self._context_manager = ConversationContextManager(
            history_max_chars=history_max_chars,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
        )

    # ── 上下文管理器协议 ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "ConversationService":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    @property
    def conversation_id(self) -> str:
        return self._context_manager.conversation_id

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    @property
    def provider(self) -> "LLMProvider":
        """底层 LLM 提供商实例（供风格切换等场景读取模型/能力信息）。"""
        return self._provider

    @property
    def supports_reasoning(self) -> bool:
        """底层 LLM 提供商是否支持思维链/推理特性（reasoning_tokens 计数）。"""
        return self._provider.supports_reasoning

    @property
    def supports_thinking(self) -> bool:
        """底层 LLM 提供商是否支持思考模式（thinking mode / reasoning_content）。

        与 ``supports_reasoning`` 的区别：
        - supports_reasoning：API 响应中是否包含 reasoning_tokens（token 级计数）
        - supports_thinking：API 是否支持思考模式（输出完整的 reasoning_content 文本）
        """
        return getattr(self._provider, "supports_thinking", False)

    @property
    def supports_vision(self) -> bool:
        """底层 LLM 提供商/模型是否支持视觉（图片）输入。

        供上层（GUI 等）决定是否提供图片上传入口。
        """
        return getattr(self._provider, "supports_vision", False)

    @property
    def last_reasoning_content(self) -> str:
        """上轮 LLM 响应的思维链推理内容（仅思考模式下有值）。"""
        return self._last_reasoning_content

    # 兼容性只读视图：暴露上下文管理器内部状态供测试/调试读取
    @property
    def _context(self) -> ConversationContext:
        return self._context_manager.context

    @property
    def _context_injection(self) -> str:
        return self._context_manager.context_injection

    async def get_usage(self) -> TokenUsage:
        """持锁获取累计 token 用量。"""
        async with self._lock:
            return TokenUsage(**self._usage.model_dump())

    async def reset_usage(self) -> None:
        """重置累计 token 用量统计。"""
        async with self._lock:
            self._usage = TokenUsage()

    async def aclose(self) -> None:
        """
        释放底层 LLM 提供商资源。

        调用 provider.aclose() 释放连接等资源。
        幂等：多次调用安全，第二次起无操作。
        推荐使用 ``async with`` 自动管理，手动调用时确保不在持锁状态下执行。
        """
        if self._closed:
            return
        self._closed = True
        await self._provider.aclose()
        logger.debug("ConversationService 已释放资源 | conversation_id=%s", self.conversation_id)

    # ── 上下文管理委托 ───────────────────────────────────────────────────────

    async def get_system_prompt(self) -> str:
        """
        获取当前系统提示词。

        Returns:
            当前系统提示词内容，未设置时返回空字符串。
        """
        return await self._context_manager.get_system_prompt()

    async def set_system_prompt(self, prompt: str) -> None:
        """
        设置或更新系统提示词。

        Args:
            prompt: 新的系统提示词内容。
        """
        await self._context_manager.set_system_prompt(prompt)

    async def set_emotion_patch(self, patch: str) -> None:
        """
        设置本轮情绪补丁，追加到 system prompt 末尾。

        每轮对话前由情感引擎调用，下一次 send()/asend() 时生效。
        传入空字符串则清除补丁。
        """
        await self._context_manager.set_emotion_patch(patch)

    async def set_context_injection(
        self,
        skills: str = "",
        tools: str = "",
        memory: str = "",
    ) -> None:
        """
        设置本轮上下文注入，三类内容按固定顺序拼接到 system prompt 末尾。

        每轮对话前调用，下一次 send()/asend() 时生效。传入空字符串则忽略该部分。
        注入内容在本轮对话结束后自动清除（通过 _clear_patches()）。

        Args:
            skills: Skills 行为指令文本
            tools: 可用工具描述列表
            memory: GRAG 检索到的记忆上下文
        """
        await self._context_manager.set_context_injection(skills=skills, tools=tools, memory=memory)

    async def get_history(self) -> list[Message]:
        """返回当前消息历史的副本（不含系统提示词）。"""
        return await self._context_manager.get_history()

    async def clear_history(self) -> None:
        """清空消息历史，保留系统提示词。"""
        await self._context_manager.clear_history()

    async def append_message(
        self,
        role: Literal["system", "user", "assistant", "tool"],
        content: str | list[dict[str, Any]],
        *,
        reasoning_content: str = "",
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """手动追加一条消息到历史，用于 brain 推理循环中注入中间结果。

        Args:
            role: 消息角色（支持 tool 角色）。
            content: 消息内容。
            reasoning_content: 思维链推理内容（DeepSeek 思考模式专有）。
                              有工具调用时必须回传，否则 API 返回 400。
            metadata: 附加元数据。
            tool_call_id: tool 角色消息关联的工具调用 ID。
            tool_calls: assistant 消息携带的工具调用数组（OpenAI 格式）。
        """
        await self._context_manager.append_message(
            role,
            content,
            reasoning_content=reasoning_content,
            metadata=metadata,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
        )

    async def discard_messages(self, content_marker: str, max_count: int) -> None:
        """
        从历史尾部向前扫描，删除匹配 metadata["prefix"] == content_marker
        且 metadata["injected"] == True 的 assistant 消息，最多删 max_count 条。

        用于清理 brain 推理循环中临时注入的中间消息（如记忆查询结果），
        防止其残留到后续对话轮次。

        Args:
            content_marker: 被注入消息的 metadata["prefix"] 值。
            max_count: 最多删除的消息数量。
        """
        await self._context_manager.discard_messages(content_marker, max_count)

    async def replace_last_message(
        self, content: str, reasoning_content: str = "",
    ) -> None:
        """替换历史最后一条消息（通常为工具阶段生成的 JSON 决策消息）。

        用于工具阶段消息净化：将纯 JSON 决策替换为纯文本标记，
        避免诱导后续阶段 LLM 模仿 JSON 输出。
        """
        await self._context_manager.replace_last_message(content, reasoning_content=reasoning_content)

    async def truncate_messages(self, keep: int) -> None:
        """截断历史，仅保留最后 keep 条消息（压缩对话时使用）。"""
        await self._context_manager.truncate_messages(keep)

    # ── 对话接口 ─────────────────────────────────────────────────────────────

    def send(self, user_input: str, images: list[str] | None = None, **kwargs) -> str:
        """
        同步发送用户消息并获取回复（仅适用于无事件循环的上下文）。

        使用 ``asyncio.run()`` 驱动异步路径，适用于脚本/REPL 等非异步上下文。
        在已有运行中事件循环的上下文（如 async 函数内部）中调用会抛出异常，
        请改用 ``await asend()``。

        Args:
            user_input: 用户输入文本。
            images: 图片 URL 或 base64 data URL 列表，用于多模态输入。
            **kwargs: 透传给 ChatRequest 的额外参数，如 temperature、max_tokens。

        Returns:
            助手回复文本。

        Raises:
            RuntimeError: 在已有运行中事件循环的上下文中调用时抛出。
            LLMRequestError: LLM 调用失败时抛出。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中循环：创建新循环驱动协程
            return asyncio.run(self.asend(user_input, images=images, **kwargs))
        else:
            raise RuntimeError(
                "send() 不能在已有运行中事件循环的上下文中调用，请改用 `await asend()`"
            )

    async def asend(self, user_input: str, images: list[str] | None = None, max_retries: int = 3, store_history: bool = True, **kwargs) -> str:
        """
        异步发送用户消息并获取回复（含指数退避重试）。

        直接调用 ``provider.async_chat_completion()``，不阻塞事件循环。
        失败时自动重试最多 max_retries 次，退避间隔为 1s / 2s / 4s ...

        如果底层提供商支持思考模式（thinking mode），会自动启用
        并捕获 ``reasoning_content`` 通过 ``last_reasoning_content`` 暴露。

        Args:
            user_input: 用户输入文本。
            images: 图片 URL 或 base64 data URL 列表，用于多模态输入；
                    为空或 None 时按纯文本消息处理。
            max_retries: 最大重试次数，默认 3。
            store_history: 为 True（默认）将用户消息写入历史；
                           为 False 则不写入，仅用现有历史继续对话。
            **kwargs: 透传给 ChatRequest 的额外参数。

        Returns:
            助手回复文本。

        Raises:
            LLMRequestError: 所有重试均失败时抛出。
        """
        add_to_history = store_history

        # 每次调用开始时清除上一轮思考内容，避免失败重试场景下残留旧值
        self._last_reasoning_content = ""

        async def prepare():
            messages = await self._prepare_request(user_input, images=images, add_to_history=add_to_history)
            return self._make_request(messages, **kwargs)

        async def execute(request):
            response = await self._provider.async_chat_completion(request)
            await self._commit_response(response.content, response.usage)
            self._last_reasoning_content = response.reasoning_content

            logger.debug(
                "收到异步对话响应 | finish_reason=%s | length=%d"
                " | prompt=%d | completion=%d | total=%d"
                " | cache_hit=%d | cache_miss=%d | reasoning=%d | reasoning_content_len=%d",
                response.finish_reason,
                len(response.content),
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                response.usage.total_tokens,
                response.usage.prompt_cache_hit_tokens,
                response.usage.prompt_cache_miss_tokens,
                response.usage.reasoning_tokens,
                len(response.reasoning_content),
            )

            return response.content

        try:
            return await async_llm_retry(
                max_retries=max_retries,
                prepare=prepare,
                execute=execute,
                on_failure=self._rollback_user_message,
                operation_name="异步对话",
            )
        finally:
            # 确保补丁在本轮结束后始终清除，不残留到下一轮
            await self._clear_patches()

    async def asend_chat(
        self, user_input: str, images: list[str] | None = None, max_retries: int = 3, store_history: bool = True, commit_content: bool = True, **kwargs
    ) -> ChatResponse:
        """异步发送并返回完整响应（含 tool_calls），供工具调度阶段使用。

        与 :meth:`asend` 的区别：
        - 返回完整的 ``ChatResponse``（含 ``tool_calls`` / ``finish_reason`` / usage），
          而 ``asend`` 仅返回回复文本，工具阶段无法读取工具调用数组。
        - 当模型返回 ``tool_calls`` 时，assistant 消息（携带 tool_calls）写入历史；
          否则当 ``commit_content=True`` 时回退为普通回复提交（同 ``asend``）。

        Args:
            user_input: 用户输入文本。
            images: 图片 URL 或 base64 data URL 列表，用于多模态输入。
            max_retries: 最大重试次数，默认 3。
            store_history: 为 True（默认）将用户消息写入历史；
                           为 False 则不写入，仅用现有历史继续对话。
            commit_content: 为 True 时，无 tool_calls 的响应作为 assistant 消息写入历史；
                            为 False 时仅累计 usage，不写历史（工具阶段临时决策用）。
            **kwargs: 透传给 ChatRequest 的额外参数（如 tools、tool_choice）。

        Returns:
            完整对话响应（含 tool_calls，可空）。

        Raises:
            LLMRequestError: 所有重试均失败时抛出。
        """
        add_to_history = store_history

        # 每次调用开始时清除上一轮思考内容，避免失败重试场景下残留旧值
        self._last_reasoning_content = ""

        async def prepare():
            messages = await self._prepare_request(user_input, images=images, add_to_history=add_to_history)
            return self._make_request(messages, **kwargs)

        async def execute(request):
            response = await self._provider.async_chat_completion(request)
            self._last_reasoning_content = response.reasoning_content
            if response.tool_calls:
                # 工具调用：assistant 消息携带 tool_calls 入历史
                await self._context_manager.append_message(
                    "assistant",
                    response.content or "",
                    reasoning_content=response.reasoning_content,
                    tool_calls=response.tool_calls,
                )
                if response.usage.prompt_tokens or response.usage.total_tokens:
                    async with self._lock:
                        self._usage += response.usage
            elif commit_content:
                await self._commit_response(response.content, response.usage)
            else:
                # 临时决策：不写历史，仅累计 usage
                if response.usage.prompt_tokens or response.usage.total_tokens:
                    async with self._lock:
                        self._usage += response.usage
            logger.debug(
                "收到异步对话响应（完整） | finish_reason=%s | tool_calls=%s",
                response.finish_reason,
                bool(response.tool_calls),
            )
            return response

        try:
            return await async_llm_retry(
                max_retries=max_retries,
                prepare=prepare,
                execute=execute,
                on_failure=self._rollback_user_message,
                operation_name="异步对话（完整响应）",
            )
        finally:
            # 确保补丁在本轮结束后始终清除，不残留到下一轮
            await self._clear_patches()

    async def astream_send(self, user_input: str, images: list[str] | None = None, max_retries: int = 3, store_history: bool = True, **kwargs) -> AsyncGenerator[str, None]:
        """
        异步流式发送用户消息，逐 token yield LLM 回复，完成后自动更新消息历史。

        直接调用 ``provider.stream_chat_completion()``，LLM 边生成边 yield，
        调用方可在生成过程中并发处理（如实时 TTS），无需等待完整回复。

        失败时自动重试最多 max_retries 次，退避间隔为 1s / 2s / 4s ...
        流式内容缓冲至完整后才写入历史，重试不会向调用方 yield 半截内容。

        Args:
            user_input: 用户输入文本。
            images: 图片 URL 或 base64 data URL 列表，用于多模态输入。
            max_retries: 最大重试次数，默认 3。
            store_history: 为 True（默认）将用户消息写入历史；
                           为 False 则不写入，仅用现有历史继续对话。
            **kwargs: 透传给 ChatRequest 的额外参数，如 temperature、max_tokens。

        Yields:
            文本片段（token）。

        Raises:
            LLMRequestError: 所有重试均失败时抛出。
        """
        add_to_history = store_history
        last_error: LLMRequestError | None = None

        try:
            for attempt in range(max_retries):
                try:
                    messages = await self._prepare_request(user_input, images=images, add_to_history=add_to_history)
                    request = self._make_request(messages, **kwargs)

                    logger.debug(
                        "异步流式对话请求 | provider=%s | messages=%d | attempt=%d/%d",
                        self._provider.provider_name,
                        len(messages),
                        attempt + 1,
                        max_retries,
                    )

                    full_reply_parts: list[str] = []
                    async for token in self._provider.stream_chat_completion(request):
                        full_reply_parts.append(token)

                    # 流式完成，无异常 — 缓冲区就绪后再 yield，防止重试时污染输出
                    full_reply = "".join(full_reply_parts)
                    for part in full_reply_parts:
                        yield part

                    stream_usage = self._provider.last_stream_usage
                    await self._commit_response(full_reply, usage=stream_usage)

                    logger.debug(
                        "异步流式对话完成 | length=%d",
                        len(full_reply),
                    )
                    return  # 成功，退出重试循环

                except LLMRequestError as e:
                    last_error = e
                    await self._rollback_user_message()

                    if attempt < max_retries - 1:
                        delay = 1.0 * (2 ** attempt)
                        logger.warning(
                            "LLM 流式调用失败，准备重试 | attempt=%d/%d | delay=%.1fs | error=%s",
                            attempt + 1,
                            max_retries,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)

            # 所有重试均失败
            assert last_error is not None
            logger.error(
                "LLM 流式调用最终失败 | attempts=%d | provider=%s | model=%s | error=%s",
                max_retries,
                self._provider.provider_name,
                self._provider.model,
                last_error,
            )
            raise last_error

        finally:
            # 确保补丁在本轮结束后始终清除，不残留到下一轮
            await self._clear_patches()

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    def _make_request(self, messages: list[dict[str, str]], **kwargs) -> ChatRequest:
        """
        构造 ChatRequest 并自动启用思考模式。

        初始请求与重试请求共用此路径，确保 thinking 参数在重试时保持一致。
        仅当 provider 支持思考模式且用户未显式禁用时自动启用。
        """
        request = ChatRequest(messages=messages, model=self._provider.model, **kwargs)
        if self.supports_thinking and request.thinking is None:
            request.thinking = {"type": "enabled"}
        return request

    async def _clear_patches(self) -> None:
        """清除本轮一次性注入的情绪/上下文补丁，确保不残留到下一轮。"""
        await self._context_manager.clear_patches()

    async def _prepare_request(
        self,
        user_input: str,
        images: list[str] | None = None,
        add_to_history: bool = True,
    ) -> list[dict[str, Any]]:
        """
        追加用户消息（可选）并返回构建好的完整消息列表，供 LLM 调用使用。

        LLM 调用在锁外执行，避免长时间持锁阻塞并发操作。

        Args:
            user_input: 用户输入文本。
            images: 图片 URL 或 base64 data URL 列表，用于多模态输入。
            add_to_history: 为 True 时将用户消息追加到历史（默认行为）；
                            为 False 时仅构建消息列表，不修改历史（transient 模式）。

        Returns:
            包含系统提示词与历史消息的完整消息列表。
        """
        return await self._context_manager.prepare_request(
            user_input, images=images, add_to_history=add_to_history
        )

    async def _rollback_user_message(self) -> None:
        """LLM 调用失败时回滚最后一条用户消息，保持 user/assistant 交替结构。"""
        await self._context_manager.rollback_user_message()

    async def _commit_response(self, content: str, usage: TokenUsage | None = None) -> None:
        """
        将助手回复追加到历史并保存上下文，同时累计 token 用量。

        Args:
            content: 助手回复的完整文本。
            usage: 可选 token 用量，用于累积统计。
        """
        await self._context_manager.commit_response(content)
        if usage is not None:
            async with self._lock:
                self._usage += usage

    # ── LLMService 协议兼容 ──────────────────────────────────────────────────

    class CompletionResponse:
        """简化响应结构，兼容 OpenAI 风格的 choices 嵌套访问。"""

        def __init__(self, content: str, usage: TokenUsage) -> None:
            self.choices = [self._Choice(content)]

        class _Choice:
            def __init__(self, content: str) -> None:
                self.message = self._Message(content)

            class _Message:
                def __init__(self, content: str) -> None:
                    self.content = content

    async def create_completion(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> CompletionResponse:
        """实现 LLMService 协议，供 Narrator 调用。

        将 messages 转换为 asend_chat 调用，返回 OpenAI 风格响应。
        """
        # 提取 response_format 放入 extra
        response_format = kwargs.pop("response_format", None)
        if response_format is not None:
            kwargs.setdefault("extra", {})["response_format"] = response_format

        # 从 messages 中提取用户消息（最后一条用户消息）
        user_input = ""
        images = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    user_input = content
                elif isinstance(content, list):
                    # 多模态内容：提取文本和图片
                    for part in content:
                        if part.get("type") == "text":
                            user_input = part.get("text", "")
                        elif part.get("type") == "image_url":
                            if images is None:
                                images = []
                            images.append(part.get("image_url", {}).get("url", ""))
                break

        # 设置系统提示词（如果有）
        system_prompt = None
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
                break

        if system_prompt:
            await self.set_system_prompt(system_prompt)

        # 调用 asend_chat
        response = await self.asend_chat(
            user_input=user_input,
            images=images,
            store_history=False,  # 不写入历史，因为这是临时调用
            **kwargs,
        )

        # 包装为 CompletionResponse
        return self.CompletionResponse(response.content, response.usage)
