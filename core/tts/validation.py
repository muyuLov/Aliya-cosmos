"""TTS 配置集中校验模块"""

from __future__ import annotations

from core.tts.exceptions import TTSConfigError


def _validate_range(name: str, value: int | float, min_val: int | float, max_val: int | float) -> None:
    """校验数值范围，不通过时抛出 TTSConfigError。"""
    if not (min_val <= value <= max_val):
        raise TTSConfigError(
            name,
            f"{name} 必须在 {min_val}-{max_val} 范围内，当前值: {value}",
        )


def _validate_choice(name: str, value: str, choices: list[str]) -> None:
    """校验枚举值，不通过时抛出 TTSConfigError。"""
    if value not in choices:
        raise TTSConfigError(
            name,
            f"{name} 必须是 {choices} 之一，当前值: {value}",
        )


# ------------------------------------------------------------------ #
# 提供商配置校验
# ------------------------------------------------------------------ #


def _validate_astra(config: dict) -> None:
    """校验 AstraTTS 提供商配置。"""
    api_url = config.get("api_url", "").strip()
    if not api_url:
        raise TTSConfigError(
            "api_url",
            "AstraTTS 提供商的 api_url 参数为必填项，不能为空",
        )
    if not (api_url.startswith("http://") or api_url.startswith("https://")):
        raise TTSConfigError(
            "api_url",
            f"api_url 必须以 http:// 或 https:// 开头，当前值: {api_url}",
        )

    chunk_size = config.get("chunk_size", 4096)
    if not isinstance(chunk_size, int) or not (512 <= chunk_size <= 1024 * 1024):
        raise TTSConfigError(
            "chunk_size",
            f"chunk_size 必须在 512-1048576 字节范围内，当前值: {chunk_size}",
        )

    timeout = config.get("timeout", 60)
    if timeout is not None and not isinstance(timeout, (int, float)):
        raise TTSConfigError(
            "timeout",
            f"timeout 必须是数字或 null，当前类型: {type(timeout).__name__}",
        )


def _validate_edge(config: dict) -> None:
    """校验 EdgeTTS 提供商配置。"""
    voice = config.get("voice", "")
    if voice and not isinstance(voice, str):
        raise TTSConfigError(
            "voice",
            f"voice 必须是字符串，当前类型: {type(voice).__name__}",
        )

    rate = config.get("rate", "+0%")
    if rate and not isinstance(rate, str):
        raise TTSConfigError(
            "rate",
            f"rate 必须是字符串（如 '+0%'），当前类型: {type(rate).__name__}",
        )

    timeout = config.get("timeout", 60)
    if not isinstance(timeout, (int, float)) or not (1 <= timeout <= 600):
        raise TTSConfigError(
            "timeout",
            f"timeout 必须在 1-600 秒范围内，当前值: {timeout}",
        )


def validate_provider_config(provider_type: str, config: dict) -> None:
    """
    集中校验提供商配置，抛出 TTSConfigError。

    Args:
        provider_type: 提供商类型（"astra" / "edge"）。
        config: 提供商配置字典。

    Raises:
        TTSConfigError: 任一参数无效时抛出。
    """
    if provider_type == "astra":
        _validate_astra(config)
    elif provider_type == "edge":
        _validate_edge(config)
    else:
        raise TTSConfigError(
            "provider_type",
            f"未知的提供商类型: {provider_type}",
        )


def validate_tts_service_config(
    prefetch_queue_size: int,
    max_concurrent_creates: int,
    prefetch_window: int,
) -> None:
    """校验 TTSService 构造参数。"""
    _validate_range("prefetch_queue_size", prefetch_queue_size, 1, 256)
    _validate_range("max_concurrent_creates", max_concurrent_creates, 1, 100)
    _validate_range("prefetch_window", prefetch_window, 1, 20)


def validate_audio_player_config(
    sample_rate: int,
    channels: int,
    pcm_format: str,
    frames_per_buffer: int,
    play_queue_size: int,
    queue_timeout: float | None,
) -> None:
    """校验 AudioPlayer 构造参数。"""
    _validate_range("sample_rate", sample_rate, 8000, 192000)

    if channels not in (1, 2):
        raise TTSConfigError(
            "channels",
            f"声道数必须为 1（单声道）或 2（立体声），当前值: {channels}",
        )

    _PCM_FORMATS = ["float32", "int16", "int32", "int8"]
    _validate_choice("pcm_format", pcm_format, _PCM_FORMATS)

    _validate_range("frames_per_buffer", frames_per_buffer, 64, 8192)
    _validate_range("play_queue_size", play_queue_size, 1, 1024)

    if queue_timeout is not None and queue_timeout < 1.0:
        raise TTSConfigError(
            "queue_timeout",
            f"队列超时必须至少 1 秒或为 None（无限等待），当前值: {queue_timeout}",
        )


__all__ = [
    "validate_provider_config",
    "validate_tts_service_config",
    "validate_audio_player_config",
]
