"""AgentContext — 会话级统一依赖容器

一次构造收拢 Agent 运行所需的全部依赖，管线各阶段 / 工具 / 钩子
订阅者均从容器取用，杜绝依赖分散与 ToolContext 重复构造。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.llm import ConversationService
from core.tts.player.core import AudioPlayer
from core.tts.service import TTSService

from agent.brain import Brain
from agent.config import AgentConfig
from agent.emotion.engine import EmotionEngine
from agent.prompts import PromptManager
from agent.prompts.style_switcher import StyleSwitcher
from agent.tools.base import ToolContext
from agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AgentContext:
    """会话级依赖容器（构造后不可变）。"""

    conv: ConversationService
    registry: ToolRegistry
    config: AgentConfig
    prompt_manager: PromptManager
    style_switcher: StyleSwitcher
    brain: Brain
    emotion: EmotionEngine
    cognition: Any | None = None  # CognitionEngine | None
    memory_manager: Any | None = None
    tts_service: TTSService | None = None
    audio_player: AudioPlayer | None = None
    audio_relay: Callable[[dict[str, object]], Awaitable[None]] | None = None
    notify: Callable[[dict[str, object]], Awaitable[None]] | None = None
    confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None
    permission_config: Any | None = None

    def make_tool_context(self) -> ToolContext:
        """派生 ToolContext（唯一构造点）。"""
        return ToolContext(
            tts_service=self.tts_service,
            audio_player=self.audio_player,
            memory_manager=self.memory_manager,
            send_message=self.notify,
            audio_relay=self.audio_relay,
            permission_config=self.permission_config,
            confirm_callback=self.confirm_callback,
        )


__all__ = ["AgentContext"]
