"""TTS 模块公共接口"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.logger import get_logger
from core.tts.constants import (
    DEFAULT_FRAMES_PER_BUFFER,
    DEFAULT_MAX_CONCURRENT_CREATES,
    DEFAULT_PLAY_QUEUE_SIZE,
    DEFAULT_PREFETCH_QUEUE_SIZE,
    DEFAULT_PREFETCH_WINDOW,
    SENTINEL,
    WAV_DETECT_SIZE,
)
from core.tts.exceptions import (
    TTSConfigError,
    TTSConnectionError,
    TTSError,
    TTSProviderNotFoundError,
    TTSRequestError,
    TTSSessionError,
)
from core.tts.models import TTSRequest, VoiceConfig
from core.tts.player.sink import AudioSink, FileAudioSink, ResilientAudioPlayer
from core.tts.player.ws_sink import WebSocketAudioSink
from core.tts.providers import AstraTTSProvider, EdgeTTSProvider, TTSProvider, TTSProviderFactory
from core.tts.service import TTSService

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from core.tts.player import AudioPlayer


TTSProviderFactory.register("astra", AstraTTSProvider)
TTSProviderFactory.register("edge", EdgeTTSProvider)


def __getattr__(name: str) -> Any:
    if name in {"AudioPlayer", "AudioPlayerError"}:
        from core.tts.player import AudioPlayer, AudioPlayerError

        return {"AudioPlayer": AudioPlayer, "AudioPlayerError": AudioPlayerError}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_player(player_config: dict[str, Any] | None = None) -> AudioPlayer | None:
    """
    根据配置字典创建 AudioPlayer 实例。

    若运行环境缺少音频硬件或 PortAudio/sounddevice 依赖，
    构建失败时将返回 ``None``（不抛异常），由上层弹性播放器切换到
    备用通道（WebSocket / 文件），避免 TTS 能力被整体禁用。

    Args:
        player_config: 支持 sample_rate、channels、pcm_format、frames_per_buffer、
            play_queue_size、queue_timeout。

    Returns:
        配置好的 AudioPlayer 实例；不可用则为 None。
    """
    from core.tts.player import AudioPlayer

    cfg = player_config or {}
    try:
        return AudioPlayer(
            **{
                k: v
                for k, v in cfg.items()
                if k in (
                    "sample_rate",
                    "channels",
                    "pcm_format",
                    "frames_per_buffer",
                    "play_queue_size",
                    "queue_timeout",
                )
            }
        )
    except Exception as e:
        _logger.warning("AudioPlayer 初始化失败（将使用备用音频通道）: %s", e)
        return None


def create_service(
    provider_name: str,
    provider_config: dict[str, Any],
    voice_config: VoiceConfig | None = None,
    prefetch_queue_size: int = DEFAULT_PREFETCH_QUEUE_SIZE,
    max_concurrent_creates: int = DEFAULT_MAX_CONCURRENT_CREATES,
    prefetch_window: int = DEFAULT_PREFETCH_WINDOW,
) -> TTSService:
    """
    根据提供商名称和配置创建 TTSService 实例。

    Args:
        provider_name: 提供商名称，如 "astra"。
        provider_config: 提供商配置字典。
        voice_config: 音色默认配置，为 None 时使用空配置。
        prefetch_queue_size: 预取队列大小，默认 16。
        max_concurrent_creates: 最大并发创建会话数，默认 10。
        prefetch_window: 滑动窗口大小，默认 3。

    Returns:
        配置好的 TTSService 实例。

    Raises:
        TTSProviderNotFoundError: 提供商名称未注册时抛出。
    """
    provider = TTSProviderFactory.create(provider_name, provider_config)
    return TTSService(
        provider=provider,
        voice_config=voice_config,
        prefetch_queue_size=prefetch_queue_size,
        max_concurrent_creates=max_concurrent_creates,
        prefetch_window=prefetch_window,
    )


def create_from_config(
    config_path: str | Path = "data/config/main.yml",
    config_prefix: str = "cosmos.service.tts",
) -> tuple[TTSService, AudioPlayer | None]:
    """
    从外部配置文件创建 TTSService 与 AudioPlayer。

    配置格式：

    .. code-block:: yaml

        tts:
          providers:
            name: astra
            config_path: data/config/TTSProviders.json
          voice: {...}
          service: {...}
          player: {...}

    Args:
        config_path: 主配置文件路径，默认 ``data/config/main.yml``。
        config_prefix: 配置节点前缀，默认 ``cosmos.service.tts``。

    Returns:
        ``(TTSService, AudioPlayer)`` 元组，均支持 ``async with`` 管理生命周期。

    Raises:
        TTSConfigError: 配置文件加载失败或格式错误时抛出。
        TTSProviderNotFoundError: 提供商未找到或配置错误时抛出。
    """
    from core.config import get_config_instance

    cfg = get_config_instance(str(config_path))
    
    # 获取 TTS 配置节点
    tts_section: dict = cfg.get(config_prefix, {})
    if not tts_section:
        raise TTSConfigError(
            "config_section",
            f"配置节点 {config_prefix} 不存在或为空"
        )
    
    # 使用工厂的统一配置加载方法
    try:
        provider_name, provider_config = TTSProviderFactory.detect_from_config(tts_section)
    except TTSProviderNotFoundError as e:
        # 将 TTSProviderNotFoundError 转换为 TTSConfigError 以保持向后兼容
        raise TTSConfigError("provider_config", str(e)) from e
    
    # 加载其他配置节点
    voice_raw: dict = tts_section.get("voice", {})
    service_config: dict = tts_section.get("service", {})
    player_config: dict = tts_section.get("player", {})

    voice_config = VoiceConfig.from_config(voice_raw)
    prefetch_queue_size = service_config.get("prefetch_queue_size", DEFAULT_PREFETCH_QUEUE_SIZE)
    max_concurrent_creates = service_config.get(
        "max_concurrent_creates", DEFAULT_MAX_CONCURRENT_CREATES
    )
    prefetch_window = service_config.get("prefetch_window", DEFAULT_PREFETCH_WINDOW)

    service = create_service(
        provider_name=provider_name,
        provider_config=provider_config,
        voice_config=voice_config,
        prefetch_queue_size=prefetch_queue_size,
        max_concurrent_creates=max_concurrent_creates,
        prefetch_window=prefetch_window,
    )
    return service, create_player(player_config)


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
