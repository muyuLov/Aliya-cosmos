"""音频特征提取：用于口型同步的 RMS 音量、频谱质心、过零率。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.logger import get_logger

_logger = get_logger(__name__)

# 噪声门阈值：低于此值视为静音
_NOISE_GATE = 0.005


@dataclass
class AudioFeatures:
    """音频特征三元组，用于口型同步驱动 Live2D 模型。"""

    volume: float = 0.0  # 归一化 RMS 音量 (0~1)
    centroid: float = 0.5  # 频谱质心比 (0~1)，低→元音(ah) 高→辅音(ss)
    zcr: float = 0.0  # 过零率归一化 (0~1)，低→浊音 高→清音

    @staticmethod
    def compute(samples: np.ndarray, sample_rate: int) -> "AudioFeatures":
        """从 PCM 样本提取口型同步特征。

        特征说明：
        - volume (0~1)：归一化 RMS 音量，驱动嘴巴开合幅度
          噪声门 + 放大 + 非线性压缩 (raw*4.0)^0.6
        - centroid (0~1)：频谱质心比，低频能量占比
          >0.7 元音(ah/oh) → 嘴张大；<0.4 辅音(ss/ff) → 嘴收窄/变形
        - zcr (0~1)：过零率归一化，区分浊音(低zcr)与清音(高zcr)

        Args:
            samples: PCM 样本数组（float64 或可安全转换的类型）。
            sample_rate: 采样率（Hz）。

        Returns:
            AudioFeatures 实例。
        """
        n = len(samples)
        if n == 0:
            return AudioFeatures()

        sr = sample_rate
        peak_max = 1.0  # float64 输入已是归一化后的值

        # ── ① RMS 音量 ──
        # np.linalg.norm 使用优化的 BLAS dot 实现，比 sqrt(mean(square()))
        # 减少一次中间数组分配
        rms = float(np.linalg.norm(samples)) / np.sqrt(float(n))
        raw_norm = rms / peak_max

        if raw_norm <= _NOISE_GATE:
            return AudioFeatures(volume=0.0, centroid=0.5, zcr=0.0)

        mapped = (raw_norm * 4.0) ** 0.6
        volume = min(1.0, mapped)

        # ── ② 频谱质心比（低频能量占比） ──
        # 降采样 4x 后 FFT：口型分析只需 <1kHz 范围，全频 FFT 浪费
        _STRIDE = 4
        decimated = samples[::_STRIDE]
        n_dec = len(decimated)
        spectrum = np.fft.rfft(decimated)
        freqs = np.fft.rfftfreq(n_dec, _STRIDE / sr) if sr else np.arange(len(spectrum))
        magnitude = np.abs(spectrum)
        total_mag = float(np.sum(magnitude))
        if total_mag > 1e-10:
            low_idx = np.searchsorted(freqs, 1000.0)
            low_mag = float(np.sum(magnitude[:low_idx]))
            centroid = low_mag / total_mag
        else:
            centroid = 0.5

        # ── ③ 过零率（归一化到 0~1） ──
        # count_nonzero(diff(signbit(samples))) 直接计数符号变化次数，
        # 比 astype + abs + sum 少两次数组分配
        zcr_raw = float(np.count_nonzero(np.diff(np.signbit(samples)))) / n
        # 归一化：过零率理论上限为采样率/2，绝大多数语音 < 0.3
        zcr = min(1.0, zcr_raw * 5.0)

        return AudioFeatures(volume=volume, centroid=centroid, zcr=zcr)
