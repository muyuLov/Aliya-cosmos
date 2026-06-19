from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.tools.base import BaseTool
from core.logger import get_logger
from core.tts.models import TTSRequest

if TYPE_CHECKING:
    from core.tts.service import TTSService
    from core.tts.player.core import AudioPlayer


logger = get_logger(__name__)


class ReplyTool(BaseTool):
    name = "reply"
    description = "向前端返回文本回复"
    input_schema = {
        "text": {"type": "string", "description": "回复文本"},
    }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"text": str(arguments.get("text", ""))}


class TTSTool(BaseTool):
    name = "tts"
    description = "调用 TTS 服务播放语音"
    input_schema = {
        "text": {"type": "string", "description": "要播报的文本"},
    }

    def __init__(self, tts_service: TTSService | None = None, audio_player: AudioPlayer | None = None) -> None:
        self._tts_service = tts_service
        self._audio_player = audio_player

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return {"played": False, "reason": "empty text"}
        if self._tts_service is None or self._audio_player is None:
            logger.warning("TTS 未配置: tts_service=%s, audio_player=%s",
                           self._tts_service is not None, self._audio_player is not None)
            return {"played": False, "reason": "TTS not configured"}
        try:
            request = TTSRequest(text=text)
            await self._audio_player.play_stream(self._tts_service.synthesize(request))
            logger.info("TTS 播放完成: len=%d", len(text))
            return {"played": True, "text": text}
        except Exception as e:
            logger.warning("TTS 播放失败: %s", e)
            return {"played": False, "error": str(e)}
