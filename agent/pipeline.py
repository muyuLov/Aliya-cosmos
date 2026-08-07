"""AgentPipeline — 管线式编排器

职责：
- 驱动一轮对话的阶段流转：before_turn → assemble → think → soul → after_turn → 统一响应
- 触发钩子（HookRegistry）接入横切能力（认知 / 情绪 / 记忆 / 通知）
- 状态通知（AgentState + 友好状态展示）与错误降级

不承担具体能力实现：记忆保存、情绪推进等均为默认钩子订阅者；
文本回复与 TTS 播放由统一响应模块（agent.response）在收尾时处理。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.logger import get_logger

from agent.context import AgentContext
from agent.hooks import HookPoint, HookRegistry
from agent.response import respond
from agent.stages.assemble import assemble_tool_phase
from agent.stages.soul import run_soul_phase
from agent.stages.think import TOOL_RESULT_MARKER, run_tool_loop

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent 循环状态"""

    IDLE = "idle"
    CONTEXT_ASSEMBLY = "context_assembly"
    THINKING = "thinking"
    TOOL_EXECUTION = "tool_execution"
    OBSERVING = "observing"
    SOUL_PHASE = "soul_phase"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


# Agent 状态 → 前端展示映射
_STATE_DISPLAY: dict[AgentState, str] = {
    AgentState.IDLE: "陪伴中",
    AgentState.CONTEXT_ASSEMBLY: "聆听中",
    AgentState.THINKING: "思考中",
    AgentState.TOOL_EXECUTION: "工作中",
    AgentState.OBSERVING: "工作中",
    AgentState.SOUL_PHASE: "思考中",
    AgentState.COMPLETED: "陪伴中",
    AgentState.ERROR: "陪伴中",
    AgentState.CANCELLED: "陪伴中",
}


@dataclass
class TurnState:
    """单轮对话的流转状态。"""

    turn: int = 0
    has_called_tools: bool = False
    last_user_input: str = ""
    final_reply: str = ""


class AgentPipeline:
    """管线式编排器：阶段流转 + 钩子触发 + 状态通知。"""

    def __init__(
        self,
        ctx: AgentContext,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._ctx = ctx
        self._hooks = hooks or HookRegistry()
        self._state: AgentState = AgentState.IDLE
        self._turn_state = TurnState()
        self._current_style: str = ctx.config.prompt_style
        self._progress_task: asyncio.Task | None = None
        self._register_default_hooks()

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def turn(self) -> int:
        return self._turn_state.turn

    @property
    def hooks(self) -> HookRegistry:
        return self._hooks

    @property
    def current_style(self) -> str:
        """当前表达风格（手动设置与自动切换均以本值为唯一来源）。"""
        return self._current_style

    @current_style.setter
    def current_style(self, style: str) -> None:
        self._current_style = style

    # ── 默认钩子订阅者（横切能力） ──────────────────────────────────────────

    def _register_default_hooks(self) -> None:
        """注册默认钩子订阅者（顺序对齐原 agent.py 主流程调用顺序）。"""
        ctx = self._ctx
        # before_turn：自动风格切换 → 认知准备
        self._hooks.register(HookPoint.BEFORE_TURN, self._hook_auto_switch_style)
        if ctx.cognition:
            self._hooks.register(HookPoint.BEFORE_TURN, ctx.cognition.before_turn)
        # after_tool：工具学习（顺序敏感，同步）
        self._hooks.register(HookPoint.AFTER_TOOL, self._hook_learn_from_tool)
        # after_turn：记忆保存 → 认知后续处理 → 情绪推进调度
        self._hooks.register(HookPoint.AFTER_TURN, self._hook_save_memory)
        if ctx.cognition:
            self._hooks.register(HookPoint.AFTER_TURN, ctx.cognition.after_turn)
        self._hooks.register(HookPoint.AFTER_TURN, self._hook_advance_emotion)

    async def _hook_auto_switch_style(self, text: str) -> None:
        """自动风格切换（before_turn 钩子）：LLM 分析用户输入推荐表达风格。"""
        ctx = self._ctx
        if not ctx.config.auto_style_enabled:
            return
        try:
            recommended = await ctx.style_switcher.analyze(
                text,
                provider=ctx.conv.provider,
            )
        except Exception as e:
            logger.warning("[AutoStyle] 自动风格切换分析失败（忽略）: %s", e)
            return
        if recommended != self._current_style:
            self._current_style = recommended
            logger.debug(
                "[AutoStyle] 自动切换风格 | text_preview=%s... → style=%s", text[:20], recommended
            )
            await self._notify(
                {
                    "type": "style_changed",
                    "style": recommended,
                    "auto": True,
                }
            )

    async def _hook_learn_from_tool(self, name: str, result: Any) -> None:
        """工具学习（after_tool 钩子）：需求更新、情景记忆、世界模型、自我模型。"""
        ctx = self._ctx
        if not ctx.cognition:
            return
        detail = result.data if result.success else result.error
        ctx.cognition.after_tool(name, result.success, detail=detail)

    async def _hook_save_memory(self, reply: str) -> None:
        """记忆保存（after_turn 钩子）。"""
        ctx = self._ctx
        if not ctx.memory_manager or not hasattr(ctx.memory_manager, "add_conversation_memory"):
            return
        try:
            day_date = time.strftime("%Y-%m-%d")
            session_id = ctx.conv.conversation_id[:12]
            await ctx.memory_manager.add_conversation_memory(
                self._turn_state.last_user_input,
                reply,
                session_id=session_id,
                day_date=day_date,
                timeline="aliya|user",
            )
        except Exception as e:
            logger.warning("记忆保存失败: %s", e)

    def _hook_advance_emotion(self, _reply: str) -> None:
        """情绪推进调度（after_turn 钩子）：后台 fire-and-forget，不阻塞收尾。

        顺序（对齐 LAAP 4.3）：先用户情绪意图，再叠加需求梯度作为微分信号。
        """
        try:
            # 快照本轮的 user_input，避免后台任务读到下一轮输入（_begin_round 会替换 _turn_state）
            user_input = self._turn_state.last_user_input
            task = asyncio.create_task(self._observe_emotion_async(user_input))
            task.add_done_callback(self._log_emotion_task_error)
        except Exception as e:
            logger.warning("[Emotion] 情绪推进调度失败: %s", e)

    async def _observe_emotion_async(self, user_input: str) -> None:
        """后台情绪推进主体：异常不向上传播。"""
        try:
            await self._observe_emotion(user_input)
            self._apply_needs_driven_emotion()
        except Exception as exc:
            logger.warning("[Emotion] 情绪推进后台任务异常: %s", exc)

    async def _observe_emotion(self, user_input: str) -> None:
        """对话完成后推进情绪状态。

        流程：分类器分析用户输入 → 推入情绪意图（nudge）→ 按经过时间推进（update）。
        """
        emotion = self._ctx.emotion
        old_emotion = emotion.current_emotion
        intent, state = await emotion.observe(user_input)

        # 情绪实际变化时 info 级别 + 推送前端，不变时 debug 级别追踪
        if emotion.current_emotion != old_emotion:
            logger.info(
                "[Emotion] 情绪变化: %s → %s | intent=%s(%s) | intensity=%.2f | vad=%s",
                old_emotion or "无",
                emotion.current_emotion,
                intent.emotion,
                intent.variant or "",
                state.intensity,
                state.current.to_dict(),
            )
            await self._notify(
                {
                    "type": "emotion_changed",
                    "emotion": emotion.current_emotion,
                    "intensity": state.intensity,
                    "vad": state.current.to_dict(),
                    "emotion_state": state.to_dict(),
                }
            )
        else:
            logger.debug(
                "[Emotion] 情绪推进 | intent=%s | dominant=%s | intensity=%.2f",
                intent.emotion,
                emotion.current_emotion,
                state.intensity,
            )

    def _apply_needs_driven_emotion(self) -> None:
        """需求驱动情绪推入（LAAP 4.3）：以增量混合进现有情绪状态机。"""
        ctx = self._ctx
        if not ctx.cognition:
            return
        try:
            gradient = ctx.cognition.compute_emotion_gradient()
            if not gradient:
                return
            ctx.emotion.apply_vad(gradient, amount=0.3)
        except Exception as e:
            logger.debug("[NeedsEmotion] 需求驱动情绪推入失败: %s", e)

    @staticmethod
    def _log_emotion_task_error(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error("[Emotion] 情绪推进任务异常: %s", task.exception())

    # ── 主入口 ──────────────────────────────────────────────────────────────

    async def handle_user_message(self, text: str) -> None:
        """处理用户消息：钩子准备 → 阶段流转 → 收尾。"""
        self._begin_round()
        self._turn_state.last_user_input = text
        final_reply = ""

        await self._notify({"type": "brain_start"})
        self._progress_task = asyncio.create_task(self._push_progress())

        try:
            # 认知准备 + 自动风格切换（before_turn）
            await self._hooks.run(HookPoint.BEFORE_TURN, text)

            # 阶段 1：上下文组装
            await self._transition(AgentState.CONTEXT_ASSEMBLY)
            await assemble_tool_phase(self._ctx)

            # 阶段 2：工具阶段循环
            await self._transition(AgentState.THINKING)
            final_reply = await run_tool_loop(
                self._ctx,
                text,
                self._turn_state,
                notify=self._notify,
                hooks=self._hooks,
            )

            # 阶段 3：灵魂阶段
            if self._turn_state.has_called_tools or not final_reply:
                await self._transition(AgentState.SOUL_PHASE)
                await self._notify({"type": "brain_progress", "message": "进入灵魂表达阶段"})
                final_reply = await run_soul_phase(
                    self._ctx,
                    style=self._current_style,
                    user_input=self._turn_state.last_user_input,
                )
                logger.debug("[Soul] 灵魂阶段回复 | reply_len=%d", len(final_reply))

            self._turn_state.final_reply = final_reply

        except asyncio.CancelledError:
            self._state = AgentState.CANCELLED
            logger.info("[Plan] Agent 循环被取消 | turn=%d", self.turn)
            raise
        except Exception as e:
            self._state = AgentState.ERROR
            logger.error("[Plan] Agent 循环异常 | turn=%d | error=%s", self.turn, e, exc_info=True)
            await self._notify({"type": "brain_error", "message": str(e)})
            if not final_reply:
                final_reply = await self._ctx.brain.force_summary_reply()
        finally:
            await self._finalize(final_reply)

    def _begin_round(self) -> None:
        self._turn_state = TurnState(last_user_input=self._turn_state.last_user_input)
        self._state = AgentState.IDLE
        self._ctx.brain.reset()
        self._ctx.brain.reset_compressed_context()

    async def _finalize(self, final_reply: str) -> None:
        """收尾：停进度、清理注入消息、触发 after_turn 钩子与统一响应。"""
        if self._progress_task and not self._progress_task.done():
            self._progress_task.cancel()
            try:
                await self._progress_task
            except asyncio.CancelledError:
                pass
        self._progress_task = None

        if self._state not in (AgentState.ERROR, AgentState.CANCELLED):
            await self._transition(AgentState.COMPLETED)
            logger.info("[Complete] 回复完成 | turn=%d | reply_len=%d", self.turn, len(final_reply))

        # 清理临时注入消息
        try:
            await self._ctx.conv.discard_messages(
                TOOL_RESULT_MARKER, self._ctx.config.max_refine_accum
            )
        except Exception:
            pass

        if final_reply:
            # after_turn：记忆保存 + 认知后续处理 + 情绪推进（同步钩子）
            await self._hooks.run(HookPoint.AFTER_TURN, final_reply)
            # 统一响应：发送文本回复 + 异步语音播放（TTS）
            await respond(final_reply, self._ctx)

    # ── 状态管理 ────────────────────────────────────────────────────────────

    async def _transition(self, new_state: AgentState) -> None:
        old_state = self._state
        if old_state != new_state:
            self._state = new_state
            await self._notify(
                {
                    "type": "state_change",
                    "from": old_state.value,
                    "to": new_state.value,
                    "turn": self.turn,
                }
            )
            new_display = _STATE_DISPLAY.get(new_state, "陪伴中")
            old_display = _STATE_DISPLAY.get(old_state, "")
            if new_display != old_display:
                await self._notify(
                    {
                        "type": "status_changed",
                        "status": new_display,
                        "state": new_state.value,
                    }
                )

    async def _push_progress(self) -> None:
        while True:
            await asyncio.sleep(self._ctx.config.progress_interval)
            if self._state in (AgentState.THINKING, AgentState.SOUL_PHASE):
                await self._notify({"type": "brain_progress", "message": "思考中"})

    async def _notify(self, data: dict) -> None:
        if self._ctx.notify:
            await self._ctx.notify(data)


__all__ = ["AgentState", "AgentPipeline", "TurnState"]
