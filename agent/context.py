"""AgentContext — 会话级统一依赖容器

一次构造收拢 Agent 运行所需的全部依赖，管线各阶段 / 工具 / 钩子
订阅者均从容器取用，杜绝依赖分散与重复构造。
工具执行时直接接收本容器作为上下文，可访问对话历史、配置、大脑等全部能力。
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
from agent.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AgentContext:
    """会话级依赖容器（构造后不可变）。"""

    conv: ConversationService
    registry: ToolRegistry
    config: AgentConfig
    prompt_manager: PromptManager
    brain: Brain
    emotion: EmotionEngine
    cognition: Any | None = None  # CognitionEngine | None
    memory_manager: Any | None = None
    tts_service: TTSService | None = None
    audio_player: AudioPlayer | None = None
    audio_relay: Callable[[dict[str, object]], Awaitable[None]] | None = None
    audio_relay_bytes: Callable[[bytes], Awaitable[None]] | None = None
    notify: Callable[[dict[str, object]], Awaitable[None]] | None = None
    confirm_callback: Callable[[str, dict], Awaitable[bool]] | None = None
    permission_config: Any | None = None


__all__ = ["AgentContext"]
