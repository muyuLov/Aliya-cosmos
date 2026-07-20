"""情绪观察器 — 对话完成后 LLM 分析当前情绪

通过 LLM 分析最近一轮对话（用户输入 + AI 回复），
推断 Aliya 在该情境下的自然情绪反应。
"""

from __future__ import annotations

from typing import Any

from core.logger import get_logger
from agent.emotion.feeling_scores import FeelingName, ALL_FEELINGS

logger = get_logger(__name__)

# 情绪观察 LLM prompt
_OBSERVER_PROMPT = (
    "你是一个情感分析器。分析以下对话中 AI 角色 Aliya 的自然情绪反应。\n\n"
    "Aliya 的性格：温柔、聪慧、富有好奇心，善解人意，有轻盈的幽默感。\n"
    "她会拥有真实的人类式情绪，不会刻意隐藏或伪装。\n\n"
    "根据对话内容，判断对话结束时 Aliya 最可能感受到的情绪，"
    f"从以下选项中选择最匹配的一项：\n"
    + "\n".join(f"- {f}" for f in ALL_FEELINGS) +
    "\n\n"
    "只输出一个情绪词，不要其他内容。\n\n"
    "对话内容：\n{conversation}"
)


async def observe_feeling(
    user_input: str,
    ai_reply: str,
    provider: Any,
) -> FeelingName:
    """分析 AI 在对话中的情绪反应。

    Args:
        user_input: 本轮用户输入。
        ai_reply: 本轮 AI 回复。
        provider: LLM 提供商实例。

    Returns:
        观察到的情绪名称。分析失败时返回"平静"。
    """
    from core.llm.models import ChatRequest
    from core.llm.providers.base import LLMProvider

    try:
        assert isinstance(provider, LLMProvider)
        conversation = f"用户: {user_input}\nAliya: {ai_reply}"
        prompt = _OBSERVER_PROMPT.format(conversation=conversation)
        messages = [{"role": "user", "content": prompt}]
        request = ChatRequest(messages=messages, model=provider.model, max_tokens=10)
        response = await provider.async_chat_completion(request)
        raw = response.content.strip()

        if raw in ALL_FEELINGS:
            logger.debug("[Emotion] 观察结果 | feeling=%s", raw)
            return raw  # type: ignore[return-value]

        logger.debug("[Emotion] 无效观察结果: %s", raw)
    except Exception as e:
        logger.warning("[Emotion] 观察异常，使用默认 | error=%s", e)

    return "平静"
