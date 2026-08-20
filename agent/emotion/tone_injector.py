"""语气注入器 — 基于当前情绪选择语气规则，注入 system prompt

对齐 Cyrene tone-injector：
  1. 根据 dominant emotion 选择语气规则片段
  2. 将规则拼接成短文本 patch
  3. 由 EmotionEngine 调用 service.set_emotion_patch() 注入

语气规则来自 data/prompts/tone-rules.md 和内置情绪映射。
"""

from __future__ import annotations

import logging
from pathlib import Path

from agent.emotion.emotion_state import EmotionState

logger = logging.getLogger(__name__)

# 情绪 → 系统 prompt 语气注入片段（自然语言，非指令式）
# 要求简短：不超过 200 字符，避免 "请" / "你必须" 等指令
_EMOTION_TONE_MAP: dict[str, str] = {
    "neutral": (
        "当前情绪基调：中性自然，保持日常对话节奏。"
    ),
    "calm": (
        "当前情绪基调：平静温和，语速偏慢，语气柔和。"
    ),
    "happy": (
        "当前情绪基调：心情愉快，语气轻快明亮，句尾可上扬。"
    ),
    "excited": (
        "当前情绪基调：有些兴奋，语速略快，表达积极热情。"
    ),
    "shy": (
        "当前情绪基调：有点害羞，语气内敛，可以用「人家」自称。"
    ),
    "affectionate": (
        "当前情绪基调：亲近柔软，语气温柔体贴，多关心对方。"
    ),
    "curious": (
        "当前情绪基调：充满好奇，主动提问，对话题感兴趣。"
    ),
    "confused": (
        "当前情绪基调：有些困惑，坦诚地表示不理解，请求解释。"
    ),
    "tired": (
        "当前情绪基调：有点疲倦，语气缓慢，偶尔叹气。"
    ),
    "sad": (
        "当前情绪基调：有些低落，语气沉闷，声音偏小。"
    ),
    "anxiety": (
        "当前情绪基调：有些焦虑，语气紧张关切，多用问句。"
    ),
    "anger": (
        "当前情绪基调：有点生气，语气直接有力，但不失分寸。"
    ),
    "concerned": (
        "当前情绪基调：有些担心，语气关切温暖，想确认对方状态。"
    ),
    "surprised": (
        "当前情绪基调：有点惊讶，语调上扬，表达意外。"
    ),
    "bored": (
        "当前情绪基调：有点无聊，语气平淡，偶尔走神。"
    ),
    "grateful": (
        "当前情绪基调：心怀感激，真诚地表达谢意。"
    ),
    "relieved": (
        "当前情绪基调：松了一口气，语气放松舒缓。"
    ),
    "disgusted": (
        "当前情绪基调：有些反感，语气微妙排斥。"
    ),
}


class ToneInjector:
    """语气注入器：根据当前情绪状态生成 system prompt 语气片段。"""

    # emotion → tone-rules.md 中的中文关键词映射
    _RULES_KEYWORDS: dict[str, str] = {
        "happy": "开心",
        "sad": "担忧",
        "anxiety": "担心",
        "confused": "困惑",
        "anger": "不同意",
        "surprised": "意外",
    }

    def __init__(self, rules_path: str | Path = "data/prompts/tone-rules.md") -> None:
        self._tone_rules_path = Path(rules_path)
        self._rules_cache: str | None = None
        self._rules_loaded = False

    def _load_rules(self) -> str:
        """加载语气规则文件内容（带缓存，只读一次）。"""
        if not self._rules_loaded:
            self._rules_loaded = True
            if self._tone_rules_path.exists():
                try:
                    self._rules_cache = self._tone_rules_path.read_text(encoding="utf-8")
                except Exception:
                    logger.warning("语气规则文件读取失败: %s", self._tone_rules_path)
                    self._rules_cache = None
        return self._rules_cache or ""

    def build_patch(self, state: EmotionState) -> str:
        """根据情绪状态生成语气注入 patch。

        Args:
            state: 当前情绪状态。

        Returns:
            将追加到 system prompt 末尾的短文本。
        """
        dominant = state.dominant
        tone_text = _EMOTION_TONE_MAP.get(dominant, _EMOTION_TONE_MAP["neutral"])

        # 追加情绪强度信息（仅高分值时）
        scores = state.scores
        intensity = scores.get(dominant, 0.0)
        if intensity > 0.7:
            tone_text += f"（强度较高：{intensity:.0%}）"

        return tone_text

    def build_patch_with_rules(self, state: EmotionState) -> str:
        """生成带情绪规则引用的增强 patch（如果 tone-rules.md 存在）。

        Returns:
            包含情绪基调 + 相关规则引用的文本。
        """
        patch = self.build_patch(state)

        # 如果 tone-rules.md 存在且对应情绪有明确规则，追加简要引用
        rules = self._load_rules()
        if rules:
            keyword = self._RULES_KEYWORDS.get(state.dominant)
            if keyword and keyword in rules:
                patch += f" 遵循语气规则中「{keyword}」相关细则。"

        return patch
