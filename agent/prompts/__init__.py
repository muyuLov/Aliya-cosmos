"""PromptManager — 分层 Prompt 管理器

负责从 `data/prompts/` 目录加载分层 prompt 文件，按两种模式组装：

模式 A — 灵魂阶段：soul + identity + system + tone-rules + styles/*
模式 B — 工具阶段：tools_system + 注入内容

支持：
- 文本缓存（避免重复 I/O）
- 风格切换（default / lively / healing / sweet）
- 情绪补丁生成
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal

from core.logger import get_logger

logger = get_logger(__name__)

# ── 类型定义 ──────────────────────────────────────────────────────────────────

StyleName = Literal["default", "lively", "healing", "sweet"]
ALL_STYLES: tuple[StyleName, ...] = ("default", "lively", "healing", "sweet")

# ── 路径常量 ──────────────────────────────────────────────────────────────────

_PROMPT_DIR = Path(__file__).parent.parent.parent / "data" / "prompts"
_STYLES_DIR = _PROMPT_DIR / "styles"

# 各层文件名
_SOUL_FILE = "soul.md"
_IDENTITY_FILE = "identity.md"
_SYSTEM_FILE = "system.md"
_TONE_FILE = "tone-rules.md"
_TOOLS_SYSTEM_FILE = "tools_system.md"

# ── 默认情绪补丁模板 ────────────────────────────────────────────────────────

# 情绪名（emotionVADPresets 的 key + soft-* 细微标签）→ 补丁文本
_EMOTION_PATCHES: dict[str, str] = {
    "neutral": "",
    "calm": "你现在心情平静而安宁，语气从容舒缓。",
    "happy": "你现在的心情很愉快，语气轻快一些，可以适当表达你的开心。",
    "excited": "你现在感到兴奋，语气更活泼、更有活力。",
    "shy": "你现在有点害羞，语气可以稍微含蓄一些。",
    "affectionate": "你现在感到内心很柔软，语气特别温柔。",
    "curious": "你现在感到好奇，语气带着探究的兴致。",
    "confused": "你现在有些困惑，语气带着疑惑。",
    "tired": "你现在有些疲惫，语气更舒缓温和。",
    "sad": "你现在心情有些低落，可以不那么勉强地保持开朗。",
    "anxiety": "你现在有些不安，语气带着紧张与关切。",
    "anger": "你现在有些不悦，语气克制而冷静，但保留真实情绪。",
    "angry": "你现在有些不悦，语气克制而冷静，但保留真实情绪。",
    "concerned": "你现在有些担心，语气中带着关切。",
    "surprised": "你现在有些惊讶，语气带着意外。",
    "bored": "你现在有点无聊，语气带着些许百无聊赖，想找点有趣的事做。",
    "grateful": "你现在心里很感激，语气真诚而温暖，把谢意表达得自然一些。",
    "relieved": "你现在终于安心了，语气比刚才轻松许多，带着如释重负的松弛。",
    "disgusted": "你现在有些反感，语气克制地表达不适，不显得过于尖刻。",
    # 细微情绪（低强度 soft-* 标签）
    "soft-happy": "你现在心情不错，语气带着一丝轻松的笑意。",
    "soft-calm": "你现在平静而安稳，语气温和平缓。",
    "soft-positive": "你现在心情偏向愉悦，语气温和。",
    "soft-uneasy": "你现在隐隐有些不安，语气带着小心。",
    "soft-low": "你现在情绪有点低落，语气比平时沉静。",
    "soft-curious": "你现在微微有些好奇，语气带着探寻。",
    "soft-shy": "你现在有些不好意思，语气更含蓄。",
    "soft-steady": "你现在沉稳笃定，语气安定。",
}


# ── PromptManager ────────────────────────────────────────────────────────────


@dataclass
class PromptManager:
    """分层 Prompt 管理器

    从 `agent/prompts/` 目录加载 prompy 文件，按需组装。结果会缓存。

    Usage::

        pm = PromptManager()
        soul_prompt = pm.build_soul_system_prompt(style="default")
        tool_prompt = pm.build_tool_system_prompt()
        patch = pm.build_emotion_patch("开心")
    """

    # 文件内容缓存
    _cache: dict[str, str] = field(default_factory=dict)

    # ── 公共组装方法 ─────────────────────────────────────────────────────

    def build_soul_system_prompt(
        self,
        style: StyleName = "default",
        tone_override: str = "",
    ) -> str:
        """构建灵魂阶段完整 system prompt。

        顺序：soul → identity → system → tone-rules → style

        Args:
            style: 表达风格名称。
            tone_override: 覆盖语气规则（可选），为空时使用 tone-rules.md。

        Returns:
            组装好的完整 system prompt 文本。
        """
        parts: list[str] = []

        # 1. 灵魂核心
        soul = self._load(_SOUL_FILE)
        if soul:
            parts.append(soul)

        # 2. 身份定位
        identity = self._load(_IDENTITY_FILE)
        if identity:
            parts.append(identity)

        # 3. 系统规则
        system = self._load(_SYSTEM_FILE)
        if system:
            parts.append(system)

        # 4. 语气规则（或覆盖）
        tone = tone_override if tone_override else self._load(_TONE_FILE)
        if tone:
            parts.append(tone)

        # 5. 表达风格
        style_content = self._load_style(style)
        if style_content:
            parts.append(style_content)

        result = "\n\n---\n\n".join(parts)
        logger.debug(
            "[Prompt] 构建灵魂阶段 prompt | style=%s | layers=%d | chars=%d",
            style, len(parts), len(result),
        )
        return result

    def build_tool_system_prompt(self) -> str:
        """构建工具阶段 system prompt。

        返回：
            工具调度规则文本（不含角色人格）。
        """
        content = self._load(_TOOLS_SYSTEM_FILE)
        logger.debug("[Prompt] 构建工具阶段 prompt | chars=%d", len(content))
        return content or ""

    def build_emotion_patch(self, feeling: str = "") -> str:
        """根据情绪名称构建情绪补丁文本。

        情绪名采用 emotion 体系（neutral/calm/happy/excited/shy/affectionate/
        curious/confused/tired/sad/anxiety/anger/concerned/surprised 及 soft-* 细微标签）。

        Args:
            feeling: 情绪名称。

        Returns:
            情绪补丁文本，空字符串表示无补丁。
        """
        if not feeling:
            return ""
        patch = _EMOTION_PATCHES.get(feeling, "")
        if not patch:
            logger.debug("[Prompt] 未知情绪名称: %s (无补丁)", feeling)
            return ""
        logger.debug("[Prompt] 构建情绪补丁 | feeling=%s | chars=%d", feeling, len(patch))
        return patch

    # ── 风格管理和切换 ──────────────────────────────────────────────────

    def list_styles(self) -> list[StyleName]:
        """返回所有可用的风格名称列表。"""
        return list(ALL_STYLES)

    def load_style_raw(self, style: StyleName) -> str:
        """读取指定风格的原始文件内容。"""
        return self._load_style(style)

    # ── 配置辅助 ─────────────────────────────────────────────────────────

    def get_config_dict(self) -> dict[str, object]:
        """返回当前状态的配置字典，便于序列化或通知前端。"""
        return {
            "styles": list(ALL_STYLES),
            "current_style": "default",  # 由外部 Agent 管理的状态
            "cache_size": len(self._cache),
            "soul_chars": len(self._load(_SOUL_FILE)),
            "tools_system_chars": len(self._load(_TOOLS_SYSTEM_FILE)),
        }

    # ── 内部方法 ─────────────────────────────────────────────────────────

    def _load(self, filename: str) -> str:
        """加载单层 prompty 文件，带缓存。"""
        if filename in self._cache:
            return self._cache[filename]

        path = _PROMPT_DIR / filename
        try:
            text = path.read_text(encoding="utf-8").strip()
            self._cache[filename] = text
            return text
        except FileNotFoundError:
            logger.warning("[Prompt] 文件未找到（跳过）: %s", path)
            return ""
        except Exception as e:
            logger.warning("[Prompt] 文件加载失败: %s | error=%s", path, e)
            return ""

    def _load_style(self, style: StyleName) -> str:
        """加载风格文件，带缓存。"""
        cache_key = f"style:{style}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        path = _STYLES_DIR / f"{style}.md"
        try:
            text = path.read_text(encoding="utf-8").strip()
            self._cache[cache_key] = text
            return text
        except FileNotFoundError:
            logger.warning("[Prompt] 风格文件未找到（回退到 default）: %s", path)
            return self._load_style("default")
        except Exception as e:
            logger.warning("[Prompt] 风格文件加载失败（回退到 default）: %s | error=%s", path, e)
            return self._load_style("default")

    def clear_cache(self) -> None:
        """清除所有缓存的内容，使下一次读取重新从磁盘加载。"""
        self._cache.clear()
        logger.debug("[Prompt] 缓存已清空")


# ── 模块级快捷函数 ──────────────────────────────────────────────────────────

_default_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """获取全局 PromptManager 单例（懒加载）。"""
    global _default_prompt_manager
    if _default_prompt_manager is None:
        _default_prompt_manager = PromptManager()
    return _default_prompt_manager


__all__ = [
    "PromptManager",
    "get_prompt_manager",
    "StyleName",
    "ALL_STYLES",
]
