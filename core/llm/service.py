"""对话服务：管理单个会话的消息历史与系统提示词"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator, Literal

from core.llm.cache import ContextCache
from core.llm.exceptions import LLMRequestError
from core.llm.models import ChatRequest, ConversationContext, Message, TokenUsage
from core.logger import get_logger

if TYPE_CHECKING:
    from core.llm.providers.base import LLMProvider

logger = get_logger(__name__)


class ConversationService:
    """
    对话服务，管理单个会话的完整生命周期。

    负责维护消息历史与系统提示词。

    **异步优先**：核心路径为 ``asend()`` / ``astream_send()``，
    均直接调用 ``provider.async_chat_completion()`` 或
    ``provider.stream_chat_completion()``，不阻塞事件循环。
    同步包装器 ``send()`` 通过 ``asyncio.run()`` 驱动异步路径，
    适用于脚本/REPL 等非异步上下文。

    **资源管理**：支持异步上下文管理器协议（``async with``），
    自动管理底层 LLM 提供商的资源生命周期（如 HTTP 连接、会话等）。

    Args:
        provider: LLM 提供商实例。
        cache: 上下文缓存实例。
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
        cache: ContextCache,
        history_max_chars: int = 90000,
        conversation_id: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._history_max_chars = history_max_chars
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self._lock = asyncio.Lock()
        self._usage: TokenUsage = TokenUsage()

        # 一次性注入补丁（每轮对话后由 _clear_patches() 清除）
        self._emotion_patch: str = ""
        self._context_injection: str = ""

        self._closed: bool = False
        self._context = self._cache.get(self.conversation_id) or ConversationContext(
            conversation_id=self.conversation_id,
            system_prompt=system_prompt,
            messages=[],
        )
        if system_prompt is not None:
            self._context.system_prompt = system_prompt
            self._save()

        # 上轮响应的 reasoning_content（DeepSeek 思考模式专有）
        self._last_reasoning_content: str = ""

    # ── 上下文管理器协议 ──────────────────────────────────────────────────────

    async def __aenter__(self) -> "ConversationService":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    @property
    def provider(self) -> Any:
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
        from core.llm.providers.openai_compatible import OpenAICompatibleProvider
        if isinstance(self._provider, OpenAICompatibleProvider):
            return self._provider.supports_thinking
        return False

    @property
    def last_reasoning_content(self) -> str:
        """上轮 LLM 响应的思维链推理内容（仅思考模式下有值）。"""
        return self._last_reasoning_content

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

        保存当前对话上下文到缓存后，调用 provider.aclose() 释放连接等资源。
        幂等：多次调用安全，第二次起无操作。
        推荐使用 ``async with`` 自动管理，手动调用时确保不在持锁状态下执行。
        """
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            self._save()
        await self._provider.aclose()
        logger.debug("ConversationService 已释放资源 | conversation_id=%s", self.conversation_id)

    # ── 公开配置接口 ─────────────────────────────────────────────────────────

    async def get_system_prompt(self) -> str:
        """
        获取当前系统提示词。

        Returns:
            当前系统提示词内容，未设置时返回空字符串。
        """
        async with self._lock:
            return self._context.system_prompt or ""

    async def set_system_prompt(self, prompt: str) -> None:
        """
        设置或更新系统提示词。

        Args:
            prompt: 新的系统提示词内容。
        """
        async with self._lock:
            self._context.system_prompt = prompt
            self._save()

    async def set_emotion_patch(self, patch: str) -> None:
        """
        设置本轮情绪补丁，追加到 system prompt 末尾。

        每轮对话前由情感引擎调用，下一次 send()/asend() 时生效。
        传入空字符串则清除补丁。
        """
        async with self._lock:
            self._emotion_patch = patch

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
        parts = []

        if skills:
            parts.append("## Available Skills")
            parts.append(skills)

        if tools:
            parts.append("## Available Tools")
            parts.append(tools)

        if memory:
            parts.append("## Relevant Memory")
            parts.append(memory)

        async with self._lock:
            self._context_injection = "\n\n".join(parts) if parts else ""

    # ── 对话接口 ─────────────────────────────────────────────────────────────

    def send(self, user_input: str, **kwargs) -> str:
        """
        同步发送用户消息并获取回复（仅适用于无事件循环的上下文）。

        使用 ``asyncio.run()`` 驱动异步路径，适用于脚本/REPL 等非异步上下文。
        在已有运行中事件循环的上下文（如 async 函数内部）中调用会抛出异常，
        请改用 ``await asend()``。

        Args:
            user_input: 用户输入文本。
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
            return asyncio.run(self.asend(user_input, **kwargs))
        else:
            raise RuntimeError(
                "send() 不能在已有运行中事件循环的上下文中调用，请改用 `await asend()`"
            )

    async def asend(self, user_input: str, max_retries: int = 3, store_history: bool = True, **kwargs) -> str:
        """
        异步发送用户消息并获取回复（含指数退避重试）。

        直接调用 ``provider.async_chat_completion()``，不阻塞事件循环。
        失败时自动重试最多 max_retries 次，退避间隔为 1s / 2s / 4s ...

        如果底层提供商支持思考模式（thinking mode），会自动启用
        并捕获 ``reasoning_content`` 通过 ``last_reasoning_content`` 暴露。

        Args:
            user_input: 用户输入文本。
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
        messages = await self._prepare_request(user_input, add_to_history=add_to_history)
        request = ChatRequest(messages=messages, model=self._provider.model, **kwargs)

        # 每次调用开始时清除上一轮思考内容，避免失败重试场景下残留旧值
        self._last_reasoning_content = ""

        # 思考模式自动启用：仅当 provider 支持且用户未显式禁用时
        if self.supports_thinking and request.thinking is None:
            request.thinking = {"type": "enabled"}

        logger.debug(
            "异步发送对话请求 | provider=%s | messages=%d | max_retries=%d | thinking=%s",
            self._provider.provider_name,
            len(messages),
            max_retries,
            request.thinking,
        )

        last_error: LLMRequestError | None = None

        try:
            for attempt in range(max_retries):
                try:
                    response = await self._provider.async_chat_completion(request)
                    await self._commit_response(response.content, response.usage)
                    self._last_reasoning_content = response.reasoning_content

                    logger.debug(
                        "收到异步对话响应 | attempt=%d | finish_reason=%s | length=%d"
                        " | prompt=%d | completion=%d | total=%d"
                        " | cache_hit=%d | cache_miss=%d | reasoning=%d | reasoning_content_len=%d",
                        attempt + 1,
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

                except LLMRequestError as e:
                    last_error = e
                    await self._rollback_user_message()
                    if attempt < max_retries - 1:
                        delay = 1.0 * (2 ** attempt)
                        logger.warning(
                            "LLM 调用失败，准备重试 | attempt=%d/%d | delay=%.1fs | error=%s",
                            attempt + 1,
                            max_retries,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)
                        # 重试时重新构建请求（用户消息已回滚）
                        messages = await self._prepare_request(user_input, add_to_history=add_to_history)
                        request = ChatRequest(
                            messages=messages, model=self._provider.model, **kwargs
                        )

            # 所有重试均失败
            assert last_error is not None
            logger.error("LLM 调用最终失败 | attempts=%d | provider=%s | model=%s | error=%s",
                         max_retries, self._provider.provider_name, self._provider.model, last_error)
            raise last_error

        finally:
            # 确保补丁在本轮结束后始终清除，不残留到下一轮
            await self._clear_patches()

    async def astream_send(self, user_input: str, max_retries: int = 3, store_history: bool = True, **kwargs) -> AsyncGenerator[str, None]:
        """
        异步流式发送用户消息，逐 token yield LLM 回复，完成后自动更新消息历史。

        直接调用 ``provider.stream_chat_completion()``，LLM 边生成边 yield，
        调用方可在生成过程中并发处理（如实时 TTS），无需等待完整回复。

        失败时自动重试最多 max_retries 次，退避间隔为 1s / 2s / 4s ...

        Args:
            user_input: 用户输入文本。
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
        messages = await self._prepare_request(user_input, add_to_history=add_to_history)

        logger.debug(
            "异步流式对话请求 | provider=%s | messages=%d | max_retries=%d",
            self._provider.provider_name,
            len(messages),
            max_retries,
        )

        last_error: LLMRequestError | None = None

        try:
            for attempt in range(max_retries):
                try:
                    full_reply_parts: list[str] = []
                    async for token in self._provider.stream_chat_completion(
                        ChatRequest(messages=messages, model=self._provider.model, **kwargs)
                    ):
                        full_reply_parts.append(token)

                    # 流式完成，无异常 — 缓冲区就绪后再 yield，防止重试时污染输出
                    full_reply = "".join(full_reply_parts)
                    # 累计流式 token 用量：provider 已将 usage 存入 last_stream_usage
                    stream_usage = self._provider.last_stream_usage
                    await self._commit_response(full_reply, usage=stream_usage)
                    for part in full_reply_parts:
                        yield part

                    logger.debug(
                        "异步流式对话完成 | attempt=%d | length=%d",
                        attempt + 1,
                        len(full_reply),
                    )
                    return

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
                        messages = await self._prepare_request(user_input, add_to_history=add_to_history)
                    else:
                        logger.error(
                            "LLM 流式调用最终失败 | attempts=%d | provider=%s | model=%s | error=%s",
                            max_retries,
                            self._provider.provider_name,
                            self._provider.model,
                            last_error,
                        )
                        raise

        finally:
            await self._clear_patches()

    # ── 历史管理 ─────────────────────────────────────────────────────────────

    async def get_history(self) -> list[Message]:
        """返回当前消息历史的副本（不含系统提示词）。"""
        async with self._lock:
            return list(self._context.messages)

    async def clear_history(self) -> None:
        """清空消息历史，保留系统提示词。"""
        async with self._lock:
            self._context.messages = []
            self._save()

    async def append_message(
        self,
        role: Literal["system", "user", "assistant"],
        content: str,
        *,
        reasoning_content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """手动追加一条消息到历史，用于 brain 推理循环中注入中间结果。

        Args:
            role: 消息角色。
            content: 消息内容。
            reasoning_content: 思维链推理内容（DeepSeek 思考模式专有）。
                              有工具调用时必须回传，否则 API 返回 400。
            metadata: 附加元数据。
        """
        async with self._lock:
            self._context.messages.append(
                Message(role=role, content=content, reasoning_content=reasoning_content, metadata=metadata)
            )
            self._context.updated_at = time.time()
            self._trim_history()
            self._save()

    async def discard_messages(self, content_marker: str, max_count: int) -> None:
        """
        从历史尾部向前扫描，删除匹配 metadata[\"prefix\"] == content_marker
        且 metadata[\"injected\"] == True 的 assistant 消息，最多删 max_count 条。

        用于清理 brain 推理循环中临时注入的中间消息（如记忆查询结果），
        防止其残留到后续对话轮次。

        Args:
            content_marker: 被注入消息的 metadata[\"prefix\"] 值。
            max_count: 最多删除的消息数量。
        """
        if max_count <= 0:
            return
        async with self._lock:
            removed = 0
            for idx in range(len(self._context.messages) - 1, -1, -1):
                if removed >= max_count:
                    break
                msg = self._context.messages[idx]
                if (
                    msg.role == "assistant"
                    and msg.metadata
                    and msg.metadata.get("injected") is True
                    and msg.metadata.get("prefix") == content_marker
                ):
                    self._context.messages.pop(idx)
                    removed += 1
            if removed:
                self._context.updated_at = time.time()
                self._save()

    async def replace_last_message(
        self, content: str, reasoning_content: str = "",
    ) -> None:
        """替换历史最后一条消息（通常为工具阶段生成的 JSON 决策消息）。

        用于工具阶段消息净化：将纯 JSON 决策替换为纯文本标记，
        避免诱导后续阶段 LLM 模仿 JSON 输出。
        """
        async with self._lock:
            if not self._context.messages:
                return
            last = self._context.messages[-1]
            replaced = last.model_copy(
                update={"content": content, "reasoning_content": reasoning_content}
            )
            self._context.messages[-1] = replaced
            self._context.updated_at = time.time()
            self._save()

    async def truncate_messages(self, keep: int) -> None:
        """截断历史，仅保留最后 keep 条消息（压缩对话时使用）。"""
        if keep < 0:
            raise ValueError("keep 必须为非负数")
        async with self._lock:
            if not self._context.messages:
                return
            self._context.messages = self._context.messages[-keep:] if keep else []
            self._context.updated_at = time.time()
            self._save()

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    async def _clear_patches(self) -> None:
        """清除本轮一次性注入的情绪/上下文补丁，确保不残留到下一轮。"""
        async with self._lock:
            self._emotion_patch = ""
            self._context_injection = ""

    async def _prepare_request(
        self,
        user_input: str,
        add_to_history: bool = True,
    ) -> list[dict[str, str]]:
        """
        追加用户消息（可选）并返回构建好的完整消息列表，供 LLM 调用使用。

        LLM 调用在锁外执行，避免长时间持锁阻塞并发操作。

        Args:
            user_input: 用户输入文本。
            add_to_history: 为 True 时将用户消息追加到历史（默认行为）；
                            为 False 时仅构建消息列表，不修改历史（transient 模式）。

        Returns:
            包含系统提示词与历史消息的完整消息列表。
        """
        async with self._lock:
            if add_to_history:
                self._context.messages.append(Message(role="user", content=user_input))
            return self._build_messages()

    async def _rollback_user_message(self) -> None:
        """LLM 调用失败时回滚最后一条用户消息，保持 user/assistant 交替结构。"""
        async with self._lock:
            if self._context.messages and self._context.messages[-1].role == "user":
                self._context.messages = self._context.messages[:-1]

    async def _commit_response(self, content: str, usage: TokenUsage | None = None) -> None:
        """
        将助手回复追加到历史并保存上下文。

        Args:
            content: 助手回复的完整文本。
            usage: 可选 token 用量，用于累积统计。
        """
        async with self._lock:
            self._context.messages.append(Message(role="assistant", content=content))
            self._context.updated_at = time.time()
            self._save()
            if usage is not None:
                self._usage += usage

    def _trim_history(self) -> None:
        """消息总字符数超限时移除最旧消息，至少保留 1 条。"""
        if self._history_max_chars <= 0 or not self._context.messages:
            return

        total = sum(len(m.content) for m in self._context.messages)
        if total <= self._history_max_chars:
            return

        idx = 0
        while idx < len(self._context.messages) - 1:
            length = len(self._context.messages[idx].content)
            total -= length
            idx += 1
            if total <= self._history_max_chars:
                break

        if idx > 0:
            self._context.messages = self._context.messages[idx:]
            self._context.updated_at = time.time()
            self._save()
            logger.info(
                "历史消息清理 | 移除 %d 条 | 剩余 %d 条（%.1fK chars）| 阈值 %d",
                idx, len(self._context.messages), total / 1000,
                self._history_max_chars,
            )

    def _build_messages(self) -> list[dict[str, str]]:
        """
        构建 LLM 请求所需的完整消息列表。

        将 system_prompt、记忆补丁、情绪补丁合并为单条 system 消息，
        再拼接历史对话消息。构建前自动清理超长历史。

        调用方须在持锁状态下调用此方法（由 _prepare_request 保证）。
        """
        self._trim_history()
        messages: list[dict[str, str]] = []

        # 合并 system 部分：prompt → 上下文注入 → 情绪补丁
        system_parts = []
        if self._context.system_prompt:
            system_parts.append(self._context.system_prompt)
        if self._context_injection:
            system_parts.append(self._context_injection)
        if self._emotion_patch:
            system_parts.append(self._emotion_patch)
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # 使用 to_full_api_dict 确保带 reasoning_content 的 assistant 消息
        # 在工具调用场景中被正确回传（符合 DeepSeek 思考模式规范）
        messages.extend(m.to_full_api_dict() for m in self._context.messages)
        return messages

    def _save(self) -> None:
        self._cache.set(self.conversation_id, self._context)
