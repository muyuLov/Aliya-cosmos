from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.tools.base import BaseTool
from core.logger import get_logger
from core.tts.models import TTSRequest

if TYPE_CHECKING:
    from agent.aliya_agent_thread import OutputChannel
    from core.tts.service import TTSService
    from core.tts.player.core import AudioPlayer


logger = get_logger(__name__)


class ReplyTool(BaseTool):
    name = "reply"
    description = "向前端返回文本回复"
    input_schema = {
        "text": {"type": "string", "description": "回复文本"},
    }

    def __init__(self, output_channel: OutputChannel | None = None) -> None:
        self._output_channel = output_channel

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = str(arguments.get("text", ""))
        if self._output_channel is not None:
            self._output_channel.send({
                "type": "reply",
                "text": text,
            })
        return {"text": text}


class TTSTool(BaseTool):
    name = "tts"
    description = "调用 TTS 服务播放语音"
    concurrency_safe = False
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

        from agent.models import ToolProgress
        from agent.tools.base import get_progress_callback
        emit_progress = get_progress_callback()
        if emit_progress:
            emit_progress(ToolProgress(self.name, "synthesizing", "语音合成中..."))

        try:
            request = TTSRequest(text=text)
            if emit_progress:
                emit_progress(ToolProgress(self.name, "playing", "语音播放中..."))
            await self._audio_player.play_stream(self._tts_service.synthesize(request))
            logger.info("TTS 播放完成: len=%d", len(text))
            if emit_progress:
                emit_progress(ToolProgress(self.name, "completed", "语音播放完成", progress=1.0))
            return {"played": True, "text": text}
        except Exception as e:
            logger.warning("TTS 播放失败: %s", e)
            return {"played": False, "error": str(e)}


class SkillTool(BaseTool):
    """显式技能调用工具：按名称引用已加载的技能指令。"""

    name = "skill"
    description = "引用已加载的技能指令来执行特定任务"
    input_schema = {
        "name": {"type": "string", "description": "技能名称"},
    }

    def __init__(self, skill_loader: Any = None) -> None:
        self._skill_loader = skill_loader

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name", "")).strip().lower()
        if not name:
            return {"success": False, "error": "技能名称为空"}

        if self._skill_loader is None:
            from agent.skill_loader import SkillLoader
            self._skill_loader = SkillLoader()

        for skill in self._skill_loader.load_all():
            if skill.name.lower() == name:
                return {"success": True, "skill": skill.name, "instructions": skill.instructions}
        return {"success": False, "error": f"未找到技能：{name}"}
