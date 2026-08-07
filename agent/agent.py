"""AliyaAgent — Agent 门面

对外 API 稳定层：包装 AgentPipeline 与 AgentContext，
WS / GUI / 测试层不感知内部管线化重构。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.logger import get_logger

from agent.brain import Brain
from agent.config import AgentConfig
from agent.context import AgentContext
from agent.emotion.engine import EmotionEngine
from agent.pipeline import AgentPipeline, AgentState
from agent.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AliyaAgent:
    """门面：对外暴露稳定 API，内部委托 AgentPipeline。"""

    def __init__(
        self,
        conversation_service: Any,
        tool_registry: ToolRegistry,
        memory_manager: Any | None = None,
        send_message: Any = None,
        tts_service: Any | None = None,
        audio_player: Any | None = None,
        audio_relay: Any = None,
        config: AgentConfig | None = None,
        confirm_callback: Any = None,
        prompt_manager: Any = None,
    ) -> None:
        from agent.prompts import get_prompt_manager
        from agent.prompts.style_switcher import get_style_switcher

        self._config = config or AgentConfig()

        # ── 大脑 / 情感引擎 / 认知引擎（与重构前一致的组装逻辑） ──
        brain = Brain(conversation_service, self._config)
        emotion = EmotionEngine(
            personality=self._config.emotion_personality,
            classifier_mode=self._config.emotion_classifier,
            max_samples_per_emotion=self._config.emotion_max_samples,
        )
        cognition = None
        if self._config.cognition_enabled:
            try:
                from agent.cognition.engine import CognitionConfig, CognitionEngine
                cognition = CognitionEngine(
                    CognitionConfig(maintenance_interval=self._config.cognition_maintenance_interval)
                )
            except Exception:
                cognition = None

        # 权限配置
        permission_config = self._init_permission_config()

        pm = prompt_manager or get_prompt_manager()
        self._ctx = AgentContext(
            conv=conversation_service,
            registry=tool_registry,
            config=self._config,
            prompt_manager=pm,
            style_switcher=get_style_switcher(),
            brain=brain,
            emotion=emotion,
            cognition=cognition,
            memory_manager=memory_manager,
            tts_service=tts_service,
            audio_player=audio_player,
            audio_relay=audio_relay,
            notify=send_message,
            confirm_callback=confirm_callback,
            permission_config=permission_config,
        )
        self._pipeline = AgentPipeline(self._ctx)
        # TTS 播放已整合进统一响应模块（pipeline 收尾时自动触发）

    # ── 对外 API（与重构前完全一致） ──

    @property
    def state(self) -> AgentState:
        return self._pipeline.state

    @property
    def turn(self) -> int:
        return self._pipeline.turn

    @property
    def cot_enabled(self) -> bool:
        return self._ctx.brain.cot_enabled

    @property
    def use_native_thinking(self) -> bool:
        return self._ctx.brain.use_native_thinking

    async def handle_user_message(self, text: str) -> None:
        await self._pipeline.handle_user_message(text)

    async def handle_clear_history(self, confirm: bool = False) -> None:
        if confirm:
            try:
                loop = asyncio.get_running_loop()
                reply = await loop.run_in_executor(
                    None, lambda: input("确认清空历史？(y/n): ").strip().lower()
                )
            except (RuntimeError, EOFError):
                reply = "y"
            if reply != "y":
                return
        await self._ctx.conv.clear_history()
        logger.info("对话历史已清空")

    def set_style(self, style: str) -> None:
        """设置表达风格，在下次灵魂阶段注入时应用。"""
        self._pipeline.current_style = style

    def get_style(self) -> str:
        return self._pipeline.current_style

    def set_emotion(self, feeling: str) -> None:
        self._ctx.emotion.set_emotion(feeling)

    def get_emotion(self) -> str:
        return self._ctx.emotion.current_emotion

    def get_emotion_state(self) -> dict:
        return self._ctx.emotion.get_state()

    async def warmup(self) -> None:
        await self._ctx.emotion.warmup()

    async def close_emotion_classifier(self) -> None:
        await self._ctx.emotion.close_classifier()

    def get_prompt_config(self) -> dict:
        return {
            "style": self._pipeline.current_style,
            "emotion": self._ctx.emotion.current_emotion or "none",
            "styles": self._ctx.prompt_manager.list_styles(),
        }

    def get_cognition_status(self) -> dict:
        if not self._ctx.cognition:
            return {"enabled": False}
        return {"enabled": True, **self._ctx.cognition.get_status()}

    # ── 辅助 ──

    def _init_permission_config(self) -> Any:
        if not self._config.permission_config_path:
            return None
        try:
            from agent.tools.permission_config import PermissionConfigManager
            return PermissionConfigManager(self._config.permission_config_path)
        except Exception:
            return None


__all__ = ["AgentState", "AliyaAgent"]
