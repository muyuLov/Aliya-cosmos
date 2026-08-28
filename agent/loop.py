"""主叙事循环（AgentLoop）：四阶段状态机

补写剧本 → 处理当前事件与行为决策 → 按模式投递 → 副作用。

替代旧两阶段 FC 循环（_tool_phase + _soul_phase），由主叙事器一次调用产出
script + 行为决策 + 结构化副产物。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncGenerator, Protocol

from agent.context import NarrativeContextBuilder
from agent.events import (
    AgentEvent,
    AlterTriggered,
    ProtocolEvent,
    RunFinished,
    RunStarted,
    TextMessageDelta,
    TextMessageEnd,
    TextMessageStart,
    TurnMetadata,
)
from agent.metadata_parser import NarrativeOutput


class NarratorProtocol(Protocol):
    """主叙事器协议。"""

    async def invoke(
        self,
        system_prompt: str,
        context_json: dict[str, Any],
        **kwargs: Any,
    ) -> NarrativeOutput: ...


class AgentLoop:
    """单个会话的主叙事循环：四阶段状态机。"""

    def __init__(
        self,
        *,
        narrator: Any = None,
        context: NarrativeContextBuilder,
        story_id: str = "default",
        participant_id: str = "user",
        service: Any = None,
        registry: Any = None,
        checker: Any = None,
        memory: Any = None,
        emotion_engine: Any = None,
        max_tool_rounds: int = 20,
        tool_timeout: float = 30.0,
        confirm_timeout: float = 30.0,
        narrator_response: Any = None,
    ) -> None:
        # 向后兼容旧接口
        self.service = service
        self.registry = registry
        self.checker = checker
        self.context = context
        self.memory = memory
        self.emotion_engine = emotion_engine
        self.max_tool_rounds = max_tool_rounds
        self.tool_timeout = tool_timeout
        self.confirm_timeout = confirm_timeout

        # 新接口
        self._narrator = narrator
        self._story_id = story_id
        self._participant_id = participant_id

        # 中断控制
        self._abort = False
        self.pending_confirmations: dict[str, asyncio.Future[bool]] = {}

        # 测试注入的预设回复
        self._narrator_response_override = narrator_response

    # ── 中断控制 ──────────────────────────────────────────

    def interrupt(self) -> None:
        self._abort = True

    def reset_abort(self) -> None:
        self._abort = False

    async def resolve_confirmation(self, call_id: str, allowed: bool) -> None:
        """外部（WS 层）响应用户确认，解除挂起。"""
        fut = self.pending_confirmations.get(call_id)
        if fut is not None and not fut.done():
            fut.set_result(allowed)

    # ── 主入口 ────────────────────────────────────────────

    async def submit_user_message(
        self, text: str, images: list[str] | None = None  # noqa: ARG002
    ) -> AsyncGenerator[AgentEvent | ProtocolEvent, None]:
        """异步生成器：四阶段状态机，逐条产出事件。"""
        self.reset_abort()
        yield RunStarted(session_id="default")

        # ── Stage 1: 构建上下文 ──
        context_json = await self.context.build_context(
            user_input=text,
            story_id=self._story_id,
            participant_id=self._participant_id,
        )

        # ── Stage 2: 主叙事调用 ──
        output = await self._invoke_narrator(context_json)

        # ── Stage 3: 按模式投递 ──
        message_id = str(uuid.uuid4())

        if output.reply_mode == "immediate" and output.reply_content:
            yield TextMessageStart(message_id=message_id)
            # 一次性投递（非流式）
            yield TextMessageDelta(message_id=message_id, text=output.reply_content)
            yield TextMessageEnd(message_id=message_id, full_text=output.reply_content)
        elif output.reply_mode == "immediate" and output.script:
            yield TextMessageStart(message_id=message_id)
            yield TextMessageDelta(message_id=message_id, text=output.script)
            yield TextMessageEnd(message_id=message_id, full_text=output.script)

        # ── Stage 4: 副作用 ──
        # TurnMetadata
        if output.memories or output.intents:
            yield TurnMetadata(
                emotion_delta=output.alter or 0,
                memory_candidates=output.memories,
                state_patches=[output.state_patch] if output.state_patch else [],
                follow_up_intents=output.intents,
            )

        # AlterTriggered
        if output.alter is not None:
            yield AlterTriggered(
                direction="",
                description="",
                intensity=float(output.alter),
            )

        yield RunFinished(session_id="default")

    async def _invoke_narrator(
        self, context_json: dict[str, Any]
    ) -> NarrativeOutput:
        """调用主叙事器。"""
        # 测试注入的预设回复
        if self._narrator_response_override is not None:
            return self._narrator_response_override

        if self._narrator is not None:
            system_prompt = self.context.build_system_prompt()
            # 支持两种调用方式：对象（.invoke）或可调用函数
            if hasattr(self._narrator, "invoke"):
                return await self._narrator.invoke(
                    system_prompt=system_prompt,
                    context_json=context_json,
                )
            else:
                # 直接调用函数
                return self._narrator(system_prompt, context_json)

        # 兜底：返回空结果
        return NarrativeOutput(reply_mode="none")
