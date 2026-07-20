"""情绪连续性系统

根据 Cyrene-Agent 的 RuntimeState 情绪平滑算法实现：
- FeelingScores：9 种情绪的分数追踪与衰减/观察平滑
- observe_feeling：对话完成后 LLM 分析当前情绪
- 快速上升情绪（担心/难过）权重更高，更快主导

情绪类型：平静 / 开心 / 温柔 / 激动 / 撒娇 / 担心 / 难过 / 感动 / 害羞
"""

from __future__ import annotations

from agent.emotion.feeling_scores import FeelingScores, FeelingName, ALL_FEELINGS, FAST_RISE
from agent.emotion.observer import observe_feeling

__all__ = [
    "FeelingScores",
    "FeelingName",
    "ALL_FEELINGS",
    "FAST_RISE",
    "observe_feeling",
]
