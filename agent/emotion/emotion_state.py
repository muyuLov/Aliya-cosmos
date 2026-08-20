"""情绪标签定义与状态数据模型

18 个 VAD 情绪标签 + angry 别名，严格对齐 GUI emotion-map.js 的 FEELING_TO_EMOTION key。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 与 GUI emotion-map.js 的 FEELING_TO_EMOTION key 严格一致（18 个基础标签）
VAD_EMOTIONS: tuple[str, ...] = (
    "neutral", "calm", "happy", "excited", "shy", "affectionate",
    "curious", "confused", "tired", "sad", "anxiety", "anger",
    "concerned", "surprised", "bored", "grateful", "relieved", "disgusted",
)

# GUI 中 angry 是 anger 的别名，归一化用
EMOTION_ALIASES: dict[str, str] = {"angry": "anger"}

# 合法标签集合（含别名目标），用于快速校验
_VALID_LABELS: frozenset[str] = frozenset(VAD_EMOTIONS)

# 负面情绪快速上升标签（对齐 Cyrene FAST_RISE）
FAST_RISE_DEFAULT: frozenset[str] = frozenset({"sad", "anxiety", "anger", "disgusted"})


def normalize_emotion(label: str) -> str | None:
    """将情绪标签归一化：别名→标准名，合法标签原样返回，非法返回 None。"""
    label = label.strip().lower()
    if label in EMOTION_ALIASES:
        return EMOTION_ALIASES[label]
    if label in _VALID_LABELS:
        return label
    return None


@dataclass
class EmotionState:
    """情绪状态快照，供 WS 查询与广播。"""

    dominant: str = "neutral"
    scores: dict[str, float] = field(default_factory=dict)


FeelingScores = dict[str, float]
