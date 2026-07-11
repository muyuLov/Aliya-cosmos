"""TTSTool — 语音合成工具"""

from __future__ import annotations

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

        try:
            from core.tts import TTSRequest

            request = TTSRequest(text=text)
            audio_chunks: list[bytes] = []

            async for chunk in context.tts_service.synthesize(request):
                audio_chunks.append(chunk)

            if not audio_chunks:
                return ToolResult(success=False, error="TTS 合成未产生音频数据")

            all_audio = b"".join(audio_chunks)

            if context.audio_player:
                context.audio_player.play_bytes(all_audio)
            if context.send_message:
                await context.send_message({
                    "type": "tts_complete",
                    "text": text,
                    "audio_size": len(all_audio),
                })

            return ToolResult(success=True)

        except Exception as e:
            return ToolResult(success=False, error=str(e))
