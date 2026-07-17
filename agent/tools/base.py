"""工具基础类型定义"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None


@dataclass
class ToolContext:
    tts_service: Any | None = None
    audio_player: Any | None = None
    memory_manager: Any | None = None
    send_message: Callable[[dict], Awaitable[None]] | None = None
    # 音频转发通道（仅 WebSocket 模式可用）；控制台模式为 None，避免向终端推送音频
    audio_relay: Callable[[dict], Awaitable[None]] | None = None


class BaseTool(Protocol):
    name: str
    description: str
    input_schema: dict

    async def execute(self, params: dict, context: ToolContext) -> ToolResult: ...
