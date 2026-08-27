"""TTS 模块公共接口"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.logger import get_logger
from core.tts.exceptions import (
    TTSConfigError,
    TTSConnectionError,
    TTSError,
    TTSProviderNotFoundError,
    TTSRequestError,
    TTSSessionError,
)
from core.tts.factory import create_from_config, create_player, create_service
from core.tts.models import TTSRequest, VoiceConfig
from core.tts.player.sink import AudioSink, FileAudioSink, ResilientAudioPlayer
from core.tts.player.ws_sink import WebSocketAudioSink
from core.tts.providers import AstraTTSProvider, EdgeTTSProvider, TTSProvider, TTSProviderFactory
from core.tts.service import TTSService

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from core.tts.player import AudioPlayer, AudioPlayerError


TTSProviderFactory.register("astra", AstraTTSProvider)
TTSProviderFactory.register("edge", EdgeTTSProvider)


def __getattr__(name: str) -> Any:
    if name in {"AudioPlayer", "AudioPlayerError"}:
        from core.tts.player import AudioPlayer, AudioPlayerError

        return {"AudioPlayer": AudioPlayer, "AudioPlayerError": AudioPlayerError}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "TTSService",
    "TTSProvider",
    "TTSProviderFactory",
    "AstraTTSProvider",
    "EdgeTTSProvider",
    "TTSRequest",
    "VoiceConfig",
    "AudioPlayer",
    "AudioPlayerError",
    "ResilientAudioPlayer",
    "FileAudioSink",
    "WebSocketAudioSink",
    "AudioSink",
    "create_service",
    "create_player",
    "create_from_config",
    "TTSError",
    "TTSProviderNotFoundError",
    "TTSConnectionError",
    "TTSRequestError",
    "TTSSessionError",
    "TTSConfigError",
]
