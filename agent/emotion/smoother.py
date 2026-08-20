"""状态平滑器 — 防止情绪标签在相邻段落间高频抖动

核心算法（对齐 Cyrene runtime-state-smoother）：
  new = prev * (1 - α) + raw * α

负面情绪快速上升标签使用加速因子加速响应：
  new = prev * (1 - α_fast) + raw * α_fast

输出归一化至 [0, 1]，每段 LLM 心跳触发一次。
"""

from __future__ import annotations

from agent.emotion.emotion_state import FAST_RISE_DEFAULT, VAD_EMOTIONS

# 各情绪的基线值（对齐 Cyrene BASELINE）
BASELINE: dict[str, float] = {e: 0.5 for e in VAD_EMOTIONS}

# 默认平滑因子（0 = 完全保守，1 = 完全跟随 LLM 原始值）
DEFAULT_ALPHA = 0.6
# 负面情绪加速因子（快起慢落）
DEFAULT_ALPHA_FAST = 0.85


def clamp01(x: float) -> float:
    """将浮点数截断到 [0, 1] 范围。"""
    return max(0.0, min(1.0, x))


def smooth_feeling(
    raw_scores: dict[str, float],
    prev_scores: dict[str, float] | None = None,
    *,
    alpha: float = DEFAULT_ALPHA,
    alpha_fast: float = DEFAULT_ALPHA_FAST,
    fast_rise_labels: frozenset[str] | None = None,
) -> dict[str, float]:
    """将 LLM 心跳返回的原始 scores 与上一次 scores 做指数移动平均。

    Args:
        raw_scores: LLM 观察器本轮返回的情绪标签 → 分值（0-1）。
        prev_scores: 上一次平滑后的 scores，None 时从基线启动。
        alpha: 默认平滑因子。
        alpha_fast: 负面情绪快速上升加速因子。
        fast_rise_labels: 需要加速上升的情绪标签集合，默认 FAST_RISE_DEFAULT。

    Returns:
        归一化到 [0, 1] 的新 scores 字典。
    """
    if prev_scores is None:
        prev_scores = dict(BASELINE)

    if fast_rise_labels is None:
        fast_rise_labels = FAST_RISE_DEFAULT

    result: dict[str, float] = {}
    for label in VAD_EMOTIONS:
        raw = raw_scores.get(label, prev_scores.get(label, 0.5))
        prev = prev_scores.get(label, 0.5)
        a = alpha_fast if label in fast_rise_labels else alpha
        new_val = prev * (1 - a) + raw * a
        result[label] = clamp01(new_val)

    return result


def dominant_emotion(scores: dict[str, float]) -> str:
    """从平滑后 scores 中取最高分标签。"""
    return max(scores, key=lambda k: scores[k])
