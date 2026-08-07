"""情绪系统

以 VAD（valence / arousal / dominance）三维向量表示情绪状态：

- VADVector / emotionVADPresets：情绪 VAD 模型与预设
- EmotionStateController：核心情绪状态机（推入 / 趋近 / 衰减 / 保持 / 环境漂移 / 推断）
- EmbeddingMessageClassifier：向量情绪分类器（集成 core.vector，语义分类，neutral 兜底）
- EmotionIntent：一次情绪推入意图
"""

from __future__ import annotations

from agent.emotion.vad import (
    VADVector,
    EmotionIntent,
    emotionVADPresets,
    getVADPreset,
    magnitude,
    nearestVADPreset,
    neutralVAD,
    weightedVADDistance,
)
from agent.emotion.emotion_state import (
    EmotionPersonality,
    EmotionStateController,
    VADRuntimeState,
)
from agent.emotion.vector_classifier import EmbeddingMessageClassifier, prewarm_emotion_corpus

__all__ = [
    "VADVector",
    "EmotionIntent",
    "emotionVADPresets",
    "getVADPreset",
    "magnitude",
    "nearestVADPreset",
    "neutralVAD",
    "weightedVADDistance",
    "EmotionPersonality",
    "EmotionStateController",
    "VADRuntimeState",
    "EmbeddingMessageClassifier",
    "prewarm_emotion_corpus",
]
