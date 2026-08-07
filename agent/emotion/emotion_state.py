"""EmotionStateController — 情绪状态控制器

核心状态机：
- 以 VAD 向量表示当前情绪（current）、目标情绪（target）与人格基线（baseline）
- nudge：根据 EmotionIntent 将 target 推向情绪预设（受 reactivity / emotionBias 调制）
- update：指数趋近 target（approach）、无保持时衰减回 baseline（decay）、环境漂移、
  并依据当前 VAD 幅度推断主导情绪
- 情绪保持（emotionHoldSeconds）：推入情绪后在一定时间内不衰减，维持情绪浓度

人格参数（EmotionPersonality）：
- baseline / reactivity / targetApproachRate / decayRate / emotionHoldSeconds
- emotionBias / ambientDriftStrength
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from agent.emotion.vad import (
    VADVector,
    EmotionIntent,
    clamp,
    clampVAD,
    getVADPreset,
    lerpVAD,
    magnitude,
    nearestVADPreset,
    seeded_random,
)


@dataclass
class EmotionPersonality:
    """人格化情绪参数（均为可选，未设置的项保持默认值）。"""

    baseline: VADVector | None = None
    reactivity: float | None = None
    targetApproachRate: float | None = None
    decayRate: float | None = None
    emotionHoldSeconds: float | None = None
    emotionBias: dict[str, float] | None = None
    ambientDriftStrength: float | None = None

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "reactivity": self.reactivity,
            "targetApproachRate": self.targetApproachRate,
            "decayRate": self.decayRate,
            "emotionHoldSeconds": self.emotionHoldSeconds,
            "emotionBias": self.emotionBias,
            "ambientDriftStrength": self.ambientDriftStrength,
        }


@dataclass
class VADRuntimeState:
    """每次 update 返回的情绪运行时状态。"""

    current: VADVector
    target: VADVector
    dominantEmotion: str
    intensity: float
    ambient: VADVector
    holdSeconds: float
    decayRate: float

    def to_dict(self) -> dict[str, object]:
        return {
            "current": self.current.to_dict(),
            "target": self.target.to_dict(),
            "dominantEmotion": self.dominantEmotion,
            "intensity": self.intensity,
            "ambient": self.ambient.to_dict(),
            "holdSeconds": self.holdSeconds,
            "decayRate": self.decayRate,
        }


def _complete_vad(value: dict[str, float] | VADVector, fallback: VADVector) -> VADVector:
    """补全不完整的 VAD 值：缺失分量取 fallback，并对结果做 clamp。"""
    if isinstance(value, VADVector):
        return clampVAD(VADVector(value.valence, value.arousal, value.dominance))
    return clampVAD(
        VADVector(
            valence=value.get("valence", fallback.valence),
            arousal=value.get("arousal", fallback.arousal),
            dominance=value.get("dominance", fallback.dominance),
        )
    )


class EmotionStateController:
    """VAD 情绪状态控制器。

    Usage::

        esc = EmotionStateController()
        esc.nudge(EmotionIntent(emotion="happy", intensity=0.75))
        state = esc.update(1 / 30)   # 每秒约 30 帧推进
        assert state.dominantEmotion == "happy"
    """

    # ── 默认配置 ────────────────────────────────────────────────────────────
    # 默认值即情绪动力学基线，可通过 EmotionPersonality 逐项覆盖。

    def __init__(self, personality: EmotionPersonality | None = None) -> None:
        # VAD 三态：current 当前值 / target 目标值 / baseline 人格基线（衰减归宿）
        self.current: VADVector = VADVector()
        self.target: VADVector = VADVector()
        self.baseline: VADVector = VADVector()

        # 环境漂移：ambientDrift 实际漂移量、ambientTarget 当前漂移目标、
        # ambientDriftStrength 漂移幅度（0 时禁用）
        self.ambientDrift: VADVector = VADVector()
        self.ambientTarget: VADVector = VADVector(0.018, -0.012, 0.01)
        self.ambientDriftStrength: float = 0.034

        # 漂移定时器 + 确定性随机源
        self.driftClock: float = 0.0
        self.nextDriftAt: float = 0.0
        self._random: Callable[[], float] = seeded_random(9137)

        # 情绪动力学：reactivity 响应性 / targetApproachRate 趋近速率 /
        # decayRate 衰减速率 / emotionHoldSeconds 情绪保持时长
        self.reactivity: float = 1.0
        self.targetApproachRate: float = 1.35
        self.decayRate: float = 0.018
        self.emotionHoldSeconds: float = 18.0

        # 保持剩余秒数（>0 时 target 不衰减）、情绪偏置、主导情绪
        self.holdRemainingSeconds: float = 0.0
        self.emotionBias: dict[str, float] = {}
        self.dominantEmotion: str = "neutral"

        if personality is not None:
            self.configure(personality)
        self.reset()

    # ── 配置 ───────────────────────────────────────────────────────────────

    def configure(self, personality: EmotionPersonality) -> None:
        """应用人格参数（各字段独立可选，缺失则保持当前值）。"""
        if personality.baseline is not None:
            self.setBaseline(personality.baseline)
        if personality.reactivity is not None:
            self.reactivity = clamp(personality.reactivity, 0.2, 2.5)
        if personality.targetApproachRate is not None:
            self.targetApproachRate = clamp(personality.targetApproachRate, 0.2, 4)
        if personality.decayRate is not None:
            self.decayRate = clamp(personality.decayRate, 2e-3, 0.4)
        if personality.emotionHoldSeconds is not None:
            self.emotionHoldSeconds = clamp(personality.emotionHoldSeconds, 0, 90)
        if personality.emotionBias is not None:
            self.emotionBias = {**self.emotionBias, **personality.emotionBias}
        if personality.ambientDriftStrength is not None:
            self.ambientDriftStrength = clamp(personality.ambientDriftStrength, 0, 0.09)

    def getDecayRate(self) -> float:
        """当前衰减速率。"""
        return self.decayRate

    def setBaseline(self, baseline: dict[str, float] | VADVector) -> None:
        """设置人格基线（缺省分量沿用当前基线）。"""
        self.baseline = _complete_vad(baseline, self.baseline)

    # ── 情绪推入 ───────────────────────────────────────────────────────────

    def nudge(self, intent: EmotionIntent) -> None:
        """根据情绪意图将 target 推向对应 VAD 预设。

        推入量 = (0.28 + intensity * 0.58) * reactivity * emotionBias，
        限制在 [0, 0.96]。同时延长情绪保持时间并设置主导情绪。
        """
        natural_emotion = intent.naturalEmotion or intent.emotion
        natural_variant = intent.naturalVariant or intent.variant

        if intent.naturalVAD is not None:
            preset = _complete_vad(intent.naturalVAD, getVADPreset(natural_emotion, natural_variant))
        else:
            preset = getVADPreset(natural_emotion, natural_variant)

        # 按情绪名（或 variant）查偏置；用键存在判断，避免偏置为 0 时被 or 回退为默认值
        bias = 1
        if natural_emotion in self.emotionBias:
            bias = self.emotionBias[natural_emotion]
        elif natural_variant and natural_variant in self.emotionBias:
            bias = self.emotionBias[natural_variant]
        amount = clamp((0.28 + intent.intensity * 0.58) * self.reactivity * bias, 0, 0.96)

        self.target = lerpVAD(self.target, preset, amount)
        self.extendHold(6 + intent.intensity * self.emotionHoldSeconds)
        self.dominantEmotion = "shy" if natural_variant and "shy" in natural_variant else natural_emotion

    def blendTo(self, target: dict[str, float] | VADVector, amount: float = 0.65) -> None:
        """将 target 向指定 VAD 混合（受 amount 控制）。"""
        clamped_amount = clamp(amount, 0, 1)
        self.target = lerpVAD(self.target, _complete_vad(target, self.target), clamped_amount)
        self.extendHold(4 + clamped_amount * self.emotionHoldSeconds)

    def nudgeVAD(self, delta: dict[str, float] | VADVector, amount: float = 1) -> None:
        """按增量调整 target 的 VAD（受 reactivity 增益）。"""
        gain = clamp(amount * self.reactivity, 0, 2)
        delta_v = _complete_vad(delta, VADVector(0.0, 0.0, 0.0))
        self.target = VADVector(
            valence=self.target.valence + delta_v.valence * gain,
            arousal=self.target.arousal + delta_v.arousal * gain,
            dominance=self.target.dominance + delta_v.dominance * gain,
        )
        self.extendHold(3 + clamp(amount, 0, 1.5) * self.emotionHoldSeconds * 0.55)

    # ── 状态更新 ───────────────────────────────────────────────────────────

    def reset(self) -> None:
        """重置到基线状态。"""
        self.current = self.baseline.copy()
        self.target = self.baseline.copy()
        self.ambientDrift = VADVector()
        self.ambientTarget = self.pickAmbientTarget()
        self.driftClock = 0.0
        self.nextDriftAt = 0.8 + self._random() * 2.1
        self.holdRemainingSeconds = 0.0
        self.dominantEmotion = "neutral"

    def update(self, deltaSeconds: float) -> VADRuntimeState:
        """推进情绪状态，返回运行时快照。

        Args:
            deltaSeconds: 距上次 update 的经过秒数。

        Returns:
            VADRuntimeState：当前/目标 VAD、主导情绪、强度、环境漂移等。
        """
        approach = 1 - math.exp(-deltaSeconds * self.targetApproachRate)
        decay = 0 if self.holdRemainingSeconds > 0 else 1 - math.exp(-deltaSeconds * self.decayRate)

        self.updateAmbientDrift(deltaSeconds)
        self.holdRemainingSeconds = max(0.0, self.holdRemainingSeconds - deltaSeconds)

        self.current = lerpVAD(self.current, self.withAmbientDrift(self.target), approach)
        self.target = lerpVAD(self.target, self.baseline, decay)

        current_magnitude = magnitude(self.current)
        if current_magnitude < 18e-4:
            self.dominantEmotion = "neutral"
        elif current_magnitude < 0.08:
            self.dominantEmotion = self.inferSubtleEmotion(self.current)
        else:
            self.dominantEmotion = self.inferDominantEmotion(self.current)

        return VADRuntimeState(
            current=self.current,
            target=self.target,
            dominantEmotion=self.dominantEmotion,
            intensity=current_magnitude,
            ambient=self.ambientDrift,
            holdSeconds=self.holdRemainingSeconds,
            decayRate=self.decayRate,
        )

    # ── 主导情绪推断 ────────────────────────────────────────────────────────

    def inferDominantEmotion(self, vad: VADVector) -> str:
        """根据 VAD 规则推断主导情绪（强信号优先于最近邻）。"""
        valence = vad.valence
        arousal = vad.arousal
        dominance = vad.dominance

        if valence > 0.12 and dominance < -0.22:
            return "shy"
        if valence < -0.34 and arousal > 0.38 and dominance < -0.12:
            return "anxiety"
        if valence < -0.42 and arousal > 0.42 and dominance > 0.18:
            return "anger"
        if valence > 0.58 and arousal > 0.62:
            return "excited"
        if valence > 0.2 and arousal < -0.24:
            return "calm"
        return nearestVADPreset(vad)

    def inferSubtleEmotion(self, vad: VADVector) -> str:
        """低强度状态下推断细微情绪标签（soft-*）。"""
        if vad.valence > 4e-3 and vad.arousal > 4e-3:
            return "soft-happy"
        if vad.valence > 4e-3 and vad.arousal < -4e-3:
            return "soft-calm"
        if vad.valence > 4e-3:
            return "soft-positive"
        if vad.valence < -4e-3 and vad.arousal > 4e-3:
            return "soft-uneasy"
        if vad.valence < -4e-3:
            return "soft-low"
        if vad.arousal > 4e-3:
            return "soft-curious"
        if vad.arousal < -4e-3:
            return "soft-calm"
        if vad.dominance < -4e-3:
            return "soft-shy"
        if vad.dominance > 4e-3:
            return "soft-steady"
        return "neutral"

    # ── 环境漂移（ambient drift） ─────────────────────────────────────────

    def updateAmbientDrift(self, deltaSeconds: float) -> None:
        """周期性更新环境漂移目标并平滑逼近。"""
        if self.ambientDriftStrength <= 0:
            return
        self.driftClock += deltaSeconds
        if self.driftClock >= self.nextDriftAt:
            self.ambientTarget = self.pickAmbientTarget()
            self.nextDriftAt = self.driftClock + 1.7 + self._random() * 4.2
        approach = 1 - math.exp(-deltaSeconds * 0.62)
        self.ambientDrift = lerpVAD(self.ambientDrift, self.ambientTarget, approach)

    def pickAmbientTarget(self) -> VADVector:
        """随机选取环境漂移目标点（受 ambientDriftStrength 缩放）。"""
        strength = self.ambientDriftStrength
        center_bias = 0.42 if self._random() < 0.26 else 1.0

        def _pick(axis_scale: float) -> float:
            half = strength * axis_scale * center_bias
            return -half + self._random() * half * 2

        return VADVector(
            valence=_pick(1),
            arousal=_pick(0.82),
            dominance=_pick(0.68),
        )

    def withAmbientDrift(self, vector: VADVector) -> VADVector:
        """叠加环境漂移分量。"""
        return VADVector(
            valence=vector.valence + self.ambientDrift.valence,
            arousal=vector.arousal + self.ambientDrift.arousal,
            dominance=vector.dominance + self.ambientDrift.dominance,
        )

    # ── 保持机制 ───────────────────────────────────────────────────────────

    def extendHold(self, durationSeconds: float) -> None:
        """延长情绪保持时间（取较大值，避免频繁推入时被缩短）。"""
        self.holdRemainingSeconds = max(self.holdRemainingSeconds, durationSeconds)


__all__ = [
    "EmotionPersonality",
    "VADRuntimeState",
    "EmotionStateController",
]
