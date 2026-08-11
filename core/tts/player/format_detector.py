"""WAV/PCM 格式检测模块"""

import io
import struct
import wave
from dataclasses import dataclass

RIFF_MAGIC = b"RIFF"

# sample_width → sounddevice dtype 字符串映射
# 注意：sounddevice 不支持 "int24"，统一用 int32 替代，在 _write_audio 中做格式转换
_WIDTH_TO_DTYPE: dict[int, str] = {
    1: "int8",
    2: "int16",
    3: "int32",  # int24 用 int32 替代（sounddevice 不原生支持 int24）
    4: "int32",
}


class WavHeaderParseError(Exception):
    """WAV 头部解析失败"""


def find_wav_data_offset(data: bytes) -> int:
    """
    扫描 WAV chunk 列表，返回 'data' chunk 的起始偏移（PCM 数据开始位置）。

    Args:
        data: 至少包含完整 WAV 头部的字节数据。

    Returns:
        'data' chunk 中 PCM 数据的起始偏移量。

    Raises:
        ValueError: 未找到 'data' chunk 时抛出。
    """
    pos = 12  # 跳过 RIFF(4) + file_size(4) + WAVE(4)
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
        if chunk_id == b"data":
            return pos + 8
        pos += 8 + chunk_size
        if chunk_size % 2 != 0:
            pos += 1
    raise ValueError("未找到 WAV data chunk")


@dataclass
class FormatInfo:
    """
    检测到的音频格式信息。

    Attributes:
        sample_rate: 采样率（Hz）。
        channels: 声道数。
        sample_width: 每个样本的字节宽度（如 2 表示 16-bit）。
        pcm_start: PCM 数据在原始字节流中的起始偏移。
        pa_format: sounddevice 使用的 dtype 字符串（如 'float32', 'int16'）。
    """

    sample_rate: int
    channels: int
    sample_width: int
    pcm_start: int
    pa_format: str


def parse_wav_header(data: bytes) -> FormatInfo:
    """
    解析 WAV 头部字节数据，返回 FormatInfo。

    Args:
        data: 至少包含完整 WAV 头部的字节数据（建议 >= 44 字节）。

    Returns:
        解析得到的 FormatInfo 对象。

    Raises:
        WavHeaderParseError: 解析失败（非标准 WAV、缺少必要字段等）时抛出。
    """

    if len(data) < 12 or data[:4] != RIFF_MAGIC or data[8:12] != b"WAVE":
        raise WavHeaderParseError("不是有效的 WAV 文件（缺少 RIFF/WAVE 标识）")

    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            sample_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
    except (wave.Error, EOFError) as e:
        raise WavHeaderParseError(f"WAV 头部解析失败: {e}") from e

    try:
        pcm_start = find_wav_data_offset(data)
    except ValueError as e:
        raise WavHeaderParseError(f"找不到 PCM 数据偏移: {e}") from e

    pa_format = _WIDTH_TO_DTYPE.get(sample_width, "int16")

    return FormatInfo(
        sample_rate=sample_rate,
        channels=channels,
        sample_width=sample_width,
        pcm_start=pcm_start,
        pa_format=pa_format,
    )

