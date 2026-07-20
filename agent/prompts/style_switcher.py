"""StyleSwitcher — 自动风格切换引擎

基于用户输入自动检测对话场景并推荐对应的表达风格。
使用 LLM 分析用户消息的意图和情绪倾向。

场景 → 风格映射：

  comfort_needed → healing（低落/疲惫/焦虑时）
  playful / praise → lively（玩笑/赞美时）
  affectionate → sweet（亲近/撒娇时）
  其他场景 → default
"""

from __future__ import annotations

from typing import Any, Literal

from core.logger import get_logger

logger = get_logger(__name__)

# ── 类型 ──────────────────────────────────────────────────────────────────────

SceneName = Literal[
    "greeting", "comfort_needed", "playful", "affectionate",
    "daily_chat", "task_focused", "farewell", "praise",
]

# ── 场景 → 风格 ───────────────────────────────────────────────────────────────

_SCENE_TO_STYLE: dict[SceneName, str] = {
    "greeting": "default",
    "comfort_needed": "healing",
    "playful": "lively",
    "affectionate": "sweet",
    "daily_chat": "default",
    "task_focused": "default",
    "farewell": "default",
    "praise": "lively",
}

# ── LLM 分析 prompt ───────────────────────────────────────────────────────────

_LLM_PROMPT = (
    "分析以下用户消息的对话场景和情绪倾向，从以下选项中选择最匹配的一项：\n"
    "- greeting：问候寒暄\n"
    "- comfort_needed：需要安慰（低落/疲惫/焦虑）\n"
    "- playful：开玩笑/调皮\n"
    "- affectionate：亲近/撒娇\n"
    "- praise：赞美表扬\n"
    "- farewell：道别\n"
    "- daily_chat：日常聊天（以上都不匹配时）\n"
    "\n"
    "只输出场景名称，不要其他内容。\n\n用户消息：{text}"
)


# ── StyleSwitcher ─────────────────────────────────────────────────────────────


class StyleSwitcher:
    """基于 LLM 的场景检测器，自动推荐表达风格。"""

    def __init__(self) -> None:
        self._last_scene: SceneName | None = None

    @property
    def last_scene(self) -> SceneName | None:
        return self._last_scene

    async def analyze(self, user_text: str, provider: Any) -> str:
        """LLM 分析用户消息，返回推荐风格名。

        Args:
            user_text: 用户输入文本。
            provider: LLM 提供商实例（为 None 时跳过分析，返回默认风格）。

        Returns:
            default / lively / healing / sweet 之一。
        """
        scene: SceneName = "daily_chat"
        if provider is not None:
            try:
                from core.llm.models import ChatRequest
                from core.llm.providers.base import LLMProvider
                assert isinstance(provider, LLMProvider)
                messages = [{"role": "user", "content": _LLM_PROMPT.format(text=user_text)}]
                request = ChatRequest(messages=messages, model=provider.model)
                response = await provider.async_chat_completion(request)
                raw = response.content.strip().lower().rstrip(",.!? \n")
                if raw in _SCENE_TO_STYLE:
                    scene = raw  # type: ignore[assignment]
                else:
                    logger.debug("[StyleSwitcher] LLM 返回无效场景: %s", raw)
            except Exception as e:
                logger.warning("[StyleSwitcher] LLM 分析异常，使用默认风格 | error=%s", e)

        self._last_scene = scene
        style = _SCENE_TO_STYLE[scene]
        if scene != "daily_chat":
            logger.debug("[StyleSwitcher] 检测结果 | scene=%s → style=%s", scene, style)
        return style


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_default_switcher: StyleSwitcher | None = None


def get_style_switcher() -> StyleSwitcher:
    """获取全局 StyleSwitcher 单例。"""
    global _default_switcher
    if _default_switcher is None:
        _default_switcher = StyleSwitcher()
    return _default_switcher


__all__ = [
    "StyleSwitcher",
    "get_style_switcher",
    "SceneName",
]
