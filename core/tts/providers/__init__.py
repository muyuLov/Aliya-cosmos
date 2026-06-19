"""TTS 提供商模块"""

from core.tts.providers.astra import AstraTTSProvider
from core.tts.providers.base import TTSProvider, TTSProviderFactory
from core.tts.providers.edge import EdgeTTSProvider

__all__ = [
    "TTSProvider",
    "TTSProviderFactory",
    "AstraTTSProvider",
    "EdgeTTSProvider",
]
