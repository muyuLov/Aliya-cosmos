"""LLM 心情观察器 — 轻量级 LLM 调用提取情绪分数

对齐 Cyrene observeRuntimeState：
  - 每次 assistant 回复后触发
  - 发送最近 4 条消息到轻量 prompt
  - 要求 LLM 返回 18 个情绪标签的 JSON 分值（0-1）
  - 异步执行，失败时降级到 neutral
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from agent.emotion.emotion_state import EMOTION_ALIASES, VAD_EMOTIONS
from agent.emotion.emotion_state import EmotionState
from agent.emotion.smoother import clamp01, smooth_feeling, dominant_emotion

if TYPE_CHECKING:
    from core.llm.providers.base import LLMProvider

from core.llm.models import ChatRequest

logger = logging.getLogger(__name__)

# 情绪观察 prompt — 要求 LLM 仅返回 JSON，无额外文本
_OBSERVE_SYSTEM_PROMPT = """\
你是一个情绪分析器。根据下面的对话片段，判断 AI 角色当前的情绪状态。
请为以下 18 个情绪标签各给出一个 0-1 之间的浮点分数，表示该情绪的强度。

情绪标签：{labels}

严格以 JSON 格式返回，不要添加任何解释文本：
{{"neutral": 0.1, "calm": 0.2, ...}}

分数之和不需要为 1，但至少有一个标签的分数应该 >= 0.3。
"""

_OBSERVE_SYSTEM = _OBSERVE_SYSTEM_PROMPT.format(labels=", ".join(VAD_EMOTIONS))

# 最近消息窗口大小
_WINDOW_SIZE = 4

# LLM 超时（秒）
_OBSERVE_TIMEOUT = 5.0

# 所有标签的默认基线分数
_DEFAULT_SCORES: dict[str, float] = {e: 0.0 for e in VAD_EMOTIONS}
_DEFAULT_SCORES["neutral"] = 1.0


class EmotionObserver:
    """轻量级 LLM 心情观察器。

    每次 assistant 回复后异步触发，提取 18 维情绪分数。
    失败时安全降级到 neutral。
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._prev_scores: dict[str, float] | None = None

    @property
    def current_state(self) -> EmotionState:
        """返回当前情绪状态快照。"""
        scores = self._prev_scores or dict(_DEFAULT_SCORES)
        return EmotionState(
            dominant=dominant_emotion(scores),
            scores=scores,
        )

    async def observe(self, messages: list[dict[str, Any]]) -> EmotionState:
        """从消息历史中提取情绪分数。

        Args:
            messages: 最近的消息列表（含 role + content）。

        Returns:
            EmotionState 快照（含平滑后的 scores）。
        """
        # 取最近 _WINDOW_SIZE 条消息
        window = messages[-_WINDOW_SIZE:] if len(messages) > _WINDOW_SIZE else messages

        # 构造对话片段供观察
        conversation = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')[:200]}"
            for m in window
            if m.get("content")
        )

        if not conversation.strip():
            return self.current_state

        try:
            raw_scores = await self._call_llm(conversation)
            self._prev_scores = smooth_feeling(raw_scores, self._prev_scores)
        except Exception:
            logger.warning("情绪观察 LLM 调用失败，降级到 neutral", exc_info=True)
            # 降级：保持上一次 scores 不变
            if self._prev_scores is None:
                self._prev_scores = dict(_DEFAULT_SCORES)

        return self.current_state

    async def _call_llm(self, conversation: str) -> dict[str, float]:
        """调用 LLM 获取原始情绪分数。"""
        request = ChatRequest(
            messages=[
                {"role": "system", "content": _OBSERVE_SYSTEM},
                {"role": "user", "content": f"以下是最近的对话片段：\n\n{conversation}"},
            ],
            model=self._provider.model,
            temperature=0.1,
            max_tokens=256,
        )

        response = await self._provider.async_chat_completion(request)
        return self._parse_scores(response.content)

    @staticmethod
    def _parse_scores(content: str) -> dict[str, float]:
        """从 LLM 响应中解析 JSON 情绪分数。"""
        # 尝试提取 JSON 块
        content = content.strip()

        # 去除可能的 markdown 代码块标记
        content = re.sub(r"```(?:json)?\s*", "", content)
        content = content.strip()

        try:
            scores_raw = json.loads(content)
        except json.JSONDecodeError:
            # 尝试找到第一个 { 和最后一个 }
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    scores_raw = json.loads(content[start:end + 1])
                except json.JSONDecodeError:
                    logger.debug("无法解析情绪分数 JSON: %s", content[:200])
                    return dict(_DEFAULT_SCORES)
            else:
                return dict(_DEFAULT_SCORES)

        # 归一化并填充
        result: dict[str, float] = {}
        for label in VAD_EMOTIONS:
            raw_val = scores_raw.get(label, 0.0)
            try:
                result[label] = clamp01(float(raw_val))
            except (TypeError, ValueError):
                result[label] = 0.0

        # 处理别名（如 angry → anger）
        for alias, canonical in EMOTION_ALIASES.items():
            if alias in scores_raw and canonical not in scores_raw:
                try:
                    result[canonical] = clamp01(float(scores_raw[alias]))
                except (TypeError, ValueError):
                    pass

        return result
