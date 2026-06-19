"""AudioPlayer 子包：流式音频播放器"""

from core.tts.player.core import AudioPlayer, AudioPlayerError
from core.tts.player.format_detector import (
    FormatInfo,
    RIFF_MAGIC,
    WavHeaderParseError,
    find_wav_data_offset,
    is_wav_format,
    parse_wav_header,
)

__all__ = [
    # 主类
    "AudioPlayer",
    "AudioPlayerError",
    # 格式检测
    "FormatInfo",
    "RIFF_MAGIC",
    "WavHeaderParseError",
    "find_wav_data_offset",
    "is_wav_format",
    "parse_wav_header",
]
