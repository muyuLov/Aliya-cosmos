"""情绪引擎模块：状态平滑器 + LLM 心情观察器 + 语气注入器

参考 Cyrene-Agent 三层机制：
1. runtime-state-smoother（状态平滑器）
2. observeRuntimeState（LLM 心情观察器）
3. tone-injector（embedding 场景匹配 + 语气注入）
"""

from agent.emotion.emotion_state import (
    EMOTION_ALIASES,
    VAD_EMOTIONS,
    EmotionState,
    FeelingScores,
)
from agent.emotion.engine import EmotionEngine, create_emotion_engine

__all__ = [
    "VAD_EMOTIONS",
    "EMOTION_ALIASES",
    "EmotionState",
    "FeelingScores",
    "EmotionEngine",
    "create_emotion_engine",
]
