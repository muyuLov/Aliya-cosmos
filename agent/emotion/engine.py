"""EmotionEngine — 情感引擎

将 Agent 的情感能力封装为独立组件：

- 情绪状态机（EmotionStateController）的推入 / 推进 / 主导情绪维护
- 向量情绪分类器（EmbeddingMessageClassifier）的懒初始化与语义分类
- 需求驱动情绪梯度（来自认知引擎）的增量混合
- set_emotion / get_state / close_classifier 等对外原语

主编排器（AliyaAgent）负责编排时序与前端通知，EmotionEngine 保持纯状态，
不依赖 send_message 回调。
"""

from __future__ import annotations

import time

from core.logger import get_logger

from agent.emotion.emotion_state import EmotionPersonality, EmotionStateController, VADRuntimeState
from agent.emotion.vector_classifier import EmbeddingMessageClassifier
from agent.emotion.vad import EmotionIntent, emotionVADPresets

logger = get_logger(__name__)


class EmotionEngine:
    """情感引擎：情绪状态机 + 向量分类器 + 需求驱动情绪。"""

    def __init__(
        self,
        personality: EmotionPersonality | None = None,
        classifier_mode: str = "auto",
        max_samples_per_emotion: int = 30,
    ) -> None:
        self._state = EmotionStateController(personality)
        # 向量情绪分类器（唯一分类器）：rule 模式不创建，vector/auto 模式创建
        self._classifier: EmbeddingMessageClassifier | None = None
        if classifier_mode in ("vector", "auto"):
            try:
                self._classifier = EmbeddingMessageClassifier(
                    max_samples_per_emotion=max_samples_per_emotion,
                )
                # 强制 vector 模式但向量配置不可用（embedding 未配置/未启用）时启动即告警，
                # 避免情绪静默降级为 neutral 而用户无从察觉
                if (
                    classifier_mode == "vector"
                    and not self._classifier.configured
                ):
                    logger.warning(
                        "[EmotionVector] classifier 已设为 vector，但向量模块不可用"
                        "（embedding 未配置或未启用），情绪将降级为 neutral"
                    )
            except Exception as exc:
                logger.warning("[EmotionVector] 分类器创建失败: %s", exc)
                self._classifier = None
        self._runtime_state: VADRuntimeState | None = None  # 最近一次推进后的情绪运行时快照
        self._last_update_at: float = time.time()
        self._current_emotion: str = "neutral"  # 当前主导情绪（emotion 名，用于情绪补丁）

    # ── 只读接口 ────────────────────────────────────────────────────────────

    @property
    def current_emotion(self) -> str:
        """当前主导情绪名称。"""
        return self._current_emotion

    @property
    def classifier(self) -> EmbeddingMessageClassifier | None:
        """向量情绪分类器（rule 模式下为 None）。"""
        return self._classifier

    # ── 情绪推进 ────────────────────────────────────────────────────────────

    async def classify(self, text: str) -> EmotionIntent:
        """情绪意图分类：懒初始化向量分类器后执行语义分类。

        向量未配置 / 不可用时，由 EmbeddingMessageClassifier 内部返回
        neutral 兜底意图（不依赖任何规则分类器）。

        Args:
            text: 用户输入文本。

        Returns:
            EmotionIntent 情绪意图。
        """
        vc = self._classifier
        if vc is None:
            return EmotionIntent(emotion="neutral", variant="neutral_ack", intensity=0.35)
        if not vc.initialized:
            try:
                await vc.initialize()
            except Exception as exc:
                logger.warning("[EmotionVector] 初始化异常，neutral 兜底: %s", exc)
        return await vc.classify(text)

    def apply_intent(self, intent: EmotionIntent, delta: float) -> VADRuntimeState:
        """推入情绪意图并推进状态机，同步对外主导情绪。

        Args:
            intent: 情绪意图（分类器输出或手动构造）。
            delta: 推进秒数。

        Returns:
            推进后的 VADRuntimeState 快照。
        """
        self._state.nudge(intent)
        state = self._state.update(delta)
        self._runtime_state = state
        self._current_emotion = self._dominant_label(state.dominantEmotion)
        return state

    def apply_vad(self, gradient: dict, amount: float = 0.3) -> VADRuntimeState | None:
        """按 VAD 梯度增量推入情绪（需求驱动情绪）。

        Args:
            gradient: {"valence": v, "arousal": a, "dominance": d}。
            amount: 混合增量（0~1）。

        Returns:
            推进后的 VADRuntimeState 快照；梯度为空时返回 None。
        """
        if not gradient:
            return None
        self._state.nudgeVAD(
            {
                "valence": gradient["valence"],
                "arousal": gradient["arousal"],
                "dominance": gradient["dominance"],
            },
            amount=amount,
        )
        state = self._state.update(2.0)
        self._runtime_state = state
        self._current_emotion = self._dominant_label(state.dominantEmotion)
        return state

    async def observe(self, user_input: str) -> tuple[EmotionIntent, VADRuntimeState]:
        """对话完成后推进情绪状态。

        流程：分类器分析用户输入 → 推入情绪意图（nudge）→ 按经过时间推进（update）。

        Returns:
            (intent, state) — 分类意图与推进后的运行时快照。
        """
        now = time.time()
        # 有效推进步长：至少 5s 保证 current 充分收敛到 target。
        # 权衡：高频连续对话（间隔 < 5s）时按最小步长而非真实间隔推进，收敛略快于真实时间，
        # 但保证情绪不会因 delta≈0 而停在中间态；间隔 > 120s 时按 120s 推进，避免单次大幅衰减。
        delta = max(5.0, min(now - self._last_update_at, 120.0))
        self._last_update_at = now

        intent = await self.classify(user_input)
        state = self.apply_intent(intent, delta)
        return intent, state

    # ── 手动设置 ────────────────────────────────────────────────────────────

    def set_emotion(self, feeling: str) -> None:
        """设置当前情绪状态，并推入情绪状态机。

        Args:
            feeling: 情绪名（neutral/calm/happy/excited/shy/affectionate/
                curious/confused/tired/sad/anxiety/anger/concerned/surprised、
                以及扩展的 bored/grateful/relieved/disgusted 等）。
        """
        self._current_emotion = feeling
        if feeling in emotionVADPresets:
            # 手动设置采用高推入强度，并推进 5s 使 current 收敛到情绪预设
            self.apply_intent(EmotionIntent(emotion=feeling, intensity=0.85), 5.0)

    # ── 状态快照 ────────────────────────────────────────────────────────────

    def get_state(self) -> dict[str, object]:
        """获取当前情绪运行时状态快照（VAD + 主导情绪 + 强度等）。

        Returns:
            VADRuntimeState 序列化字典。
        """
        if self._runtime_state is None:
            self._runtime_state = self._state.update(0)
        return self._runtime_state.to_dict()

    # ── 资源管理 ────────────────────────────────────────────────────────────

    async def warmup(self) -> None:
        """预热：提前向量化入库情绪语料，避免首次对话时同步等待。

        幂等；分类器未创建 / 向量未配置 / 已就绪时直接返回。
        与 classify 首次触发并发执行时由分类器内部锁保证只入库一次。
        """
        vc = self._classifier
        if vc is None or not vc.configured or vc.initialized:
            return
        try:
            await vc.initialize()
        except Exception as exc:
            logger.warning("[EmotionVector] 预热入库失败: %s", exc)

    async def close_classifier(self) -> None:
        """释放向量情绪分类器的底层资源（Embedding API 客户端）。

        幂等；应用退出或配置热重载前调用。
        """
        vc, self._classifier = self._classifier, None
        if vc is not None:
            aclose = getattr(vc, "aclose", None)
            if aclose is not None:
                await aclose()

    # ── 内部辅助 ────────────────────────────────────────────────────────────

    @staticmethod
    def _dominant_label(raw: str) -> str:
        """将主导情绪标签归一化用于对外展示。

        soft-* 是低强度细微情绪标签（magnitude < 0.08），
        对"当前主导情绪"语义而言归为 neutral。
        """
        return raw if not raw.startswith("soft-") else "neutral"


__all__ = ["EmotionEngine"]
