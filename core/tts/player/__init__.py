"""AudioPlayer 子包：流式音频播放器"""

from core.tts.player.audio_features import AudioFeatures
from core.tts.player.core import AudioPlayer, AudioPlayerError
from core.tts.player.format_detector import (
    RIFF_MAGIC,
    FormatInfo,
    WavHeaderParseError,
    find_wav_data_offset,
    parse_wav_header,
)
from core.tts.player.mp3_decoder import Mp3StreamDecoder

__all__ = [
    # 主类
    "AudioPlayer",
    "AudioPlayerError",
    # 格式检测
    "FormatInfo",
    "RIFF_MAGIC",
    "WavHeaderParseError",
    "find_wav_data_offset",
    "parse_wav_header",
    # MP3 解码
    "Mp3StreamDecoder",
    # 音频特征
    "AudioFeatures",
]
