"""TTSTool — 语音合成工具"""

from __future__ import annotations

from typing import AsyncIterator

from agent.tools.base import BaseTool, ToolContext, ToolResult


class TTSTool:
    name = "tts_speak"
    description = "将文本合成为语音并播放。当需要语音回复时，与 reply 工具同时调用。"
    input_schema: dict = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "需要朗读的文本内容",
            },
        },
        "required": ["text"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        text = params["text"]

        if not context.tts_service:
            return ToolResult(success=False, error="TTS 服务不可用")
        if not context.audio_player:
            return ToolResult(success=False, error="音频播放器不可用")

        from core.tts import TTSRequest

        try:
            request = TTSRequest(text=text)
            total_bytes = 0

            async def _count(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
                nonlocal total_bytes
                async for chunk in chunks:
                    total_bytes += len(chunk)
                    yield chunk

            # 流式合成 + 流式播放：边接收音频块边播放，
            # 避免全量缓冲，降低首字节延迟与内存占用
            await context.audio_player.play_stream(
                _count(context.tts_service.synthesize(request))
            )

            if total_bytes == 0:
                return ToolResult(success=False, error="TTS 合成未产生音频数据")

            if context.send_message:
                await context.send_message({
                    "type": "tts_complete",
                    "text": text,
                    "audio_size": total_bytes,
                })

            return ToolResult(success=True)

        except Exception as e:
            return ToolResult(success=False, error=str(e))
