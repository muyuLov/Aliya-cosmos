"""对话上下文管理器：单会话上下文状态、消息历史与注入补丁的独立管理"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

from core.llm.cache import ContextCache
from core.llm.models import ConversationContext, Message, make_multimodal_content
from core.logger import get_logger

logger = get_logger(__name__)


class ConversationContextManager:
    """
    对话上下文管理器，负责单个会话的全部上下文职责。

    职责范围：
    - 维护消息历史与系统提示词（ConversationContext）
    - 管理一次性注入补丁（情绪补丁、上下文注入），本轮对话后自动清除
    - 超长历史自动裁剪（history_max_chars 字符阈值）
    - 构建 LLM 请求所需的完整消息列表（合并 system + 注入 + 补丁）
    - 上下文持久化到 ContextCache

    **线程模型**：所有公开方法内部持锁，保证并发安全；
    LLM 调用（provider 请求）由 ConversationService 在锁外执行，
    避免长时间持锁阻塞并发操作。

    **资源生命周期**：本类不管理 provider 资源，仅负责上下文状态；
    会话关闭时调用 :meth:`persist` 保存最终快照。

    Args:
        cache: 上下文缓存实例。
        history_max_chars: 消息历史总字符数阈值，超限时移除最旧消息，默认 90000。
        conversation_id: 会话唯一标识符，为 None 时自动生成 UUID。
        system_prompt: 初始系统提示词，会覆盖缓存中的旧值。
    """

    def __init__(
        self,
        cache: ContextCache,
        *,
        history_max_chars: int = 90000,
        conversation_id: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._cache = cache
        self._lock = asyncio.Lock()
        self._history_max_chars = history_max_chars

        # 一次性注入补丁（每轮对话后由 clear_patches() 清除）
        self._emotion_patch: str = ""
        self._context_injection: str = ""

        self._conversation_id = conversation_id or str(uuid.uuid4())
        self._context = cache.get(self._conversation_id) or ConversationContext(
            conversation_id=self._conversation_id,
            system_prompt=system_prompt,
            messages=[],
        )
        if system_prompt is not None:
            self._context.system_prompt = system_prompt
            self._save()

    # ── 状态暴露 ─────────────────────────────────────────────────────────────

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def context(self) -> ConversationContext:
        return self._context

    @property
    def context_injection(self) -> str:
        return self._context_injection

    # ── 系统提示词 ───────────────────────────────────────────────────────────

    async def get_system_prompt(self) -> str:
        """获取当前系统提示词，未设置时返回空字符串。"""
        async with self._lock:
            return self._context.system_prompt or ""

    async def set_system_prompt(self, prompt: str) -> None:
        """设置或更新系统提示词。"""
        async with self._lock:
            self._context.system_prompt = prompt
            self._save()

    # ── 一次性注入补丁 ───────────────────────────────────────────────────────

    async def set_emotion_patch(self, patch: str) -> None:
        """
        设置本轮情绪补丁，追加到 system prompt 末尾。

        每轮对话前由情感引擎调用，下一次 prepare_request() 时生效。
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

        每轮对话前调用，下一次 prepare_request() 时生效。传入空字符串则忽略该部分。
        注入内容在本轮对话结束后自动清除（通过 clear_patches()）。

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

    async def clear_patches(self) -> None:
        """清除本轮一次性注入的情绪/上下文补丁，确保不残留到下一轮。"""
        async with self._lock:
            self._emotion_patch = ""
            self._context_injection = ""

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
        role: Literal["system", "user", "assistant", "tool"],
        content: str | list[dict[str, Any]],
        *,
        reasoning_content: str = "",
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """手动追加一条消息到历史。

        Args:
            role: 消息角色。
            content: 消息内容（多模态消息传入 OpenAI 视觉格式 content 数组）。
            reasoning_content: 思维链推理内容（DeepSeek 思考模式专有）。
                              有工具调用时必须回传，否则 API 返回 400。
            metadata: 附加元数据。
            tool_call_id: tool 角色消息关联的工具调用 ID（role == "tool" 时必填）。
            tool_calls: assistant 消息携带的工具调用数组（OpenAI 格式）。
        """
        async with self._lock:
            self._context.messages.append(
                Message(
                    role=role,
                    content=content,
                    reasoning_content=reasoning_content,
                    metadata=metadata,
                    tool_call_id=tool_call_id,
                    tool_calls=tool_calls,
                )
            )
            self._context.updated_at = time.time()
            # 裁剪发生时会内部保存上下文，避免重复写缓存
            if not self._trim_history():
                self._save()

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

    async def replace_last_message(self, content: str, reasoning_content: str = "") -> None:
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

    # ── 请求构建与事务 ───────────────────────────────────────────────────────

    async def prepare_request(
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
            images: 图片 URL 或 base64 data URL 列表。传入时用户消息以
                    OpenAI 视觉格式（content 数组）追加，实现多模态输入。
            add_to_history: 为 True 时将用户消息追加到历史（默认行为）；
                            为 False 时仅构建消息列表，不修改历史（transient 模式）。

        Returns:
            包含系统提示词与历史消息的完整消息列表。
        """
        async with self._lock:
            if add_to_history:
                content: str | list[dict[str, Any]] = user_input
                if images:
                    content = make_multimodal_content(user_input, images)
                self._context.messages.append(Message(role="user", content=content))
            return self._build_messages()

    async def rollback_user_message(self) -> None:
        """LLM 调用失败时回滚最后一条用户消息，保持 user/assistant 交替结构。"""
        async with self._lock:
            if self._context.messages and self._context.messages[-1].role == "user":
                self._context.messages = self._context.messages[:-1]

    async def commit_response(self, content: str) -> None:
        """将助手回复追加到历史并保存上下文。"""
        async with self._lock:
            self._context.messages.append(Message(role="assistant", content=content))
            self._context.updated_at = time.time()
            self._save()

    async def persist(self) -> None:
        """保存当前上下文快照到缓存（会话关闭时调用）。"""
        async with self._lock:
            self._save()

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    def _save(self) -> None:
        """将当前上下文写入缓存。调用方须在持锁状态下调用。"""
        self._cache.set(self._context.conversation_id, self._context)

    def _trim_history(self) -> bool:
        """
        消息总字符数超限时移除最旧消息，至少保留 1 条。调用方须持锁。

        Returns:
            是否发生了裁剪。裁剪发生时内部已保存上下文。
        """
        if self._history_max_chars <= 0 or not self._context.messages:
            return False

        total = sum(self._content_length(m.content) for m in self._context.messages)
        if total <= self._history_max_chars:
            return False

        idx = 0
        while idx < len(self._context.messages) - 1:
            length = self._content_length(self._context.messages[idx].content)
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
            return True

        return False

    @staticmethod
    def _content_length(content: str | list[dict[str, Any]]) -> int:
        """计算消息内容字符数（兼容多模态 content 数组）。"""
        if isinstance(content, str):
            return len(content)
        return sum(len(str(part)) for part in content)

    def _build_messages(self) -> list[dict[str, Any]]:
        """
        构建 LLM 请求所需的完整消息列表。

        将 system_prompt、上下文注入、情绪补丁合并为单条 system 消息，
        再拼接历史对话消息。构建前自动清理超长历史。
        调用方须在持锁状态下调用此方法（由 prepare_request 保证）。
        """
        self._trim_history()
        messages: list[dict[str, Any]] = []

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
