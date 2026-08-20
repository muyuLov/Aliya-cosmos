"""情绪引擎 — 装配观察器 + 平滑器 + 注入器

EmotionEngine 是情绪子系统的门面（Facade）：
  - 接收 assistant 回复后的消息历史
  - 调用 EmotionObserver 提取原始分数
  - 经 smooth_feeling 平滑
  - 由 ToneInjector 生成语气 patch
  - 调用 service.set_emotion_patch() 注入到下一轮 system prompt
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.emotion.emotion_state import EmotionState
from agent.emotion.observer import EmotionObserver
from agent.emotion.tone_injector import ToneInjector

if TYPE_CHECKING:
    from core.llm.providers.base import LLMProvider
    from core.llm.service import ConversationService

logger = logging.getLogger(__name__)


class EmotionEngine:
    """情绪引擎：观察 → 平滑 → 注入。"""

    def __init__(self, provider: LLMProvider) -> None:
        self._observer = EmotionObserver(provider)
        self._injector = ToneInjector()
        self._service: ConversationService | None = None

    def bind_service(self, service: ConversationService) -> None:
        """绑定 ConversationService，用于注入语气 patch。"""
        self._service = service

    @property
    def current_state(self) -> EmotionState:
        """返回当前情绪状态快照（供 WS 查询）。"""
        return self._observer.current_state

    async def on_turn_complete(self, messages: list[dict[str, Any]]) -> EmotionState:
        """一轮对话完成后调用：观察 → 平滑 → 注入。

        Args:
            messages: 当前消息历史（role + content 格式）。

        Returns:
            最新的 EmotionState。
        """
        # 1. LLM 心跳观察
        state = await self._observer.observe(messages)

        # 2. 生成语气 patch
        patch = self._injector.build_patch(state)

        # 3. 注入到 service（如果有绑定）
        if self._service is not None:
            try:
                await self._service.set_emotion_patch(patch)
            except Exception:
                logger.warning("语气注入失败", exc_info=True)

        logger.debug(
            "情绪引擎更新 | dominant=%s | intensity=%.2f | patch_len=%d",
            state.dominant,
            state.scores.get(state.dominant, 0.0),
            len(patch),
        )

        return state


def create_emotion_engine(provider: LLMProvider) -> EmotionEngine:
    """工厂函数：创建 EmotionEngine 实例。"""
    return EmotionEngine(provider)
