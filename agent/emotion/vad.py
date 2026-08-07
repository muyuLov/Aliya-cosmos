"""VAD 情绪模型

情绪以三维向量表示：valence（效价）、arousal（唤醒度）、dominance（支配度），
取值范围均为 [-1, 1]。

理论基础：PAD 三维情感模型（Mehrabian & Russell, 1974），
- P（Pleasure）愉悦度 → valence
- A（Arousal）激活度 → arousal
- D（Dominance）优势度 → dominance

八分位组合（+P +A +D 高兴 / -P -A -D 无聊 / +P +A -D 依赖 / -P -A +D 蔑视 /
+P -A +D 放松 / -P +A -D 焦虑 / +P -A -D 温顺 / -P +A +D 敌意）。
基础预设与八分位一一对应；扩展预设（bored/grateful/relieved/disgusted）
同样遵循该八分位，填补陪伴场景的高频情绪空缺。

包含：
- VADVector：三维情绪向量
- emotionVADPresets：情绪名称 → VAD 预设
- getVADPreset：按 emotion + variant 解析 VAD 预设
- 向量运算：clampVAD / lerpVAD / magnitude / weightedVADDistance / nearestVADPreset
- seededRandom：确定性随机源（LCG）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable

# ── 基础数值工具 ─────────────────────────────────────────────────────────────


def clamp(value: float, lo: float = -float("inf"), hi: float = float("inf")) -> float:
    """将 value 限制在 [lo, hi] 区间。"""
    return max(lo, min(hi, value))


def clamp01(value: float) -> float:
    """限制到 [0, 1]。"""
    return clamp(value, 0, 1)


def lerp(from_: float, to: float, t: float) -> float:
    """线性插值。"""
    return from_ + (to - from_) * t


def seeded_random(seed: float) -> Callable[[], float]:
    """确定性随机源（LCG）。

    初始值取 seed 绝对值的整数部分（为 0 时用 1），
    每次调用返回 [0, 1) 内的伪随机数。
    """
    value = abs(int(seed)) or 1

    def _next() -> float:
        nonlocal value
        value = (value * 9301 + 49297) % 233280
        return value / 233280

    return _next


# ── VAD 向量 ──────────────────────────────────────────────────────────────────


@dataclass
class VADVector:
    """三维情绪向量（valence / arousal / dominance，均在 [-1, 1]）。"""

    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def copy(self) -> "VADVector":
        """返回副本（dataclass 可变，显式拷贝避免别名共享）。"""
        return VADVector(self.valence, self.arousal, self.dominance)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, float]) -> "VADVector":
        """从（可缺省的）字典构造，缺失分量取 0。"""
        return VADVector(
            valence=float(data.get("valence", 0.0)),
            arousal=float(data.get("arousal", 0.0)),
            dominance=float(data.get("dominance", 0.0)),
        )


# 中性 VAD（零向量，作为独立常量供外部引用；预设与 fallback 使用独立实例避免别名共享）
neutralVAD = VADVector()


def _vad(v: float, a: float, d: float) -> VADVector:
    """按 valence / arousal / dominance 顺序构造预设向量的简写。"""
    return VADVector(v, a, d)


# ── 情绪 VAD 预设 ─────────────────────────────────────────────────────────────
# 前 15 个为基础情绪预设；后 4 个为扩展预设（基于 PAD 八分位理论，标注对应
# 组合），填补陪伴场景的高频情绪空缺。

emotionVADPresets: dict[str, VADVector] = {
    # ── 基础情绪预设 ──
    "neutral": VADVector(),  # 独立实例，与 neutralVAD 常量解耦避免别名共享
    "calm": _vad(0.25, -0.45, 0.2),          # +P -A +D 放松
    "happy": _vad(0.75, 0.45, 0.35),         # +P +A +D 高兴
    "excited": _vad(0.85, 0.85, 0.45),       # +P +A +D 兴奋
    "shy": _vad(0.35, 0.6, -0.45),           # +P +A -D 依赖/害羞
    "affectionate": _vad(0.65, 0.1, 0.1),    # +P +A +D 温情
    "curious": _vad(0.35, 0.55, 0.2),        # +P +A +D 好奇
    "confused": _vad(-0.1, 0.35, -0.3),      # -P +A -D 困惑
    "tired": _vad(-0.25, -0.7, -0.3),        # -P -A -D 疲惫
    "sad": _vad(-0.65, -0.45, -0.5),         # -P -A -D 悲伤
    "anxiety": _vad(-0.6, 0.7, -0.55),       # -P +A -D 焦虑
    "anger": _vad(-0.7, 0.75, 0.55),         # -P +A +D 愤怒
    "angry": _vad(-0.7, 0.75, 0.55),         # anger 的别名
    "concerned": _vad(-0.18, 0.28, -0.2),    # -P +A -D 担忧
    "surprised": _vad(0.18, 0.78, -0.08),    # +P +A -D 惊讶
    # ── 扩展预设（PAD 八分位，参考 Mehrabian 标准值，经 nearestVADPreset
    #    距离校验：确保推入后主导情绪可稳定判为该情绪，不被邻近预设抢占） ──
    "bored": _vad(-0.55, -0.55, -0.45),      # -P -A -D 无聊
    "grateful": _vad(0.8, 0.35, -0.2),       # +P +A -D 感激（谦逊姿态）
    "relieved": _vad(0.55, -0.3, 0.15),      # +P -A +D 安心
    "disgusted": _vad(-0.5, 0.45, 0.25),     # -P +A +D 厌恶（比 anger 温和）
}


def getVADPreset(emotion: str, variant: str | None = None) -> VADVector:
    """按 emotion + variant 解析 VAD 预设。

    variant 特殊规则：
    - 含 "shy" → shy
    - 含 "comfort" → emotion 为 concerned 时用 concerned，否则 affectionate
    - 含 "startled" → surprised
    - 其余按 emotion 查表，查不到回退 neutral
    """
    if variant and "shy" in variant:
        return emotionVADPresets["shy"]
    if variant and "comfort" in variant:
        return emotionVADPresets["concerned"] if emotion == "concerned" else emotionVADPresets["affectionate"]
    if variant and "startled" in variant:
        return emotionVADPresets["surprised"]
    # 未命中时返回预设中的 neutral 独立实例（不共享 neutralVAD 常量，避免别名污染）
    return emotionVADPresets.get(emotion, emotionVADPresets["neutral"])


# ── 向量运算 ──────────────────────────────────────────────────────────────────


def clampVAD(vector: VADVector) -> VADVector:
    """将向量各分量限制到 [-1, 1]。"""
    return VADVector(
        valence=clamp(vector.valence, -1, 1),
        arousal=clamp(vector.arousal, -1, 1),
        dominance=clamp(vector.dominance, -1, 1),
    )


def lerpVAD(from_: VADVector, to: VADVector, amount: float) -> VADVector:
    """向量线性插值。"""
    return clampVAD(
        VADVector(
            valence=lerp(from_.valence, to.valence, amount),
            arousal=lerp(from_.arousal, to.arousal, amount),
            dominance=lerp(from_.dominance, to.dominance, amount),
        )
    )


def magnitude(vector: VADVector) -> float:
    """计算情绪强度（加权模长，归一化到 [0, 1]）。"""
    return clamp(
        (abs(vector.valence) + abs(vector.arousal) * 0.82 + abs(vector.dominance) * 0.64) / 2.46,
        0,
        1,
    )


def weightedVADDistance(a: VADVector, b: VADVector) -> float:
    """加权欧氏距离平方。"""
    v = a.valence - b.valence
    ar = a.arousal - b.arousal
    d = a.dominance - b.dominance
    return v * v * 1.08 + ar * ar * 0.88 + d * d * 1.28


def nearestVADPreset(vad: VADVector) -> str:
    """在情绪预设中找最近的（排除 neutral 与 angry 别名）。

    最近距离小于 0.92 时返回该情绪，否则返回 "neutral"。
    """
    candidates = [
        (emotion, preset)
        for emotion, preset in emotionVADPresets.items()
        if emotion != "neutral" and emotion != "angry"
    ]
    best_emotion = "neutral"
    best_distance = float("inf")
    for emotion, preset in candidates:
        distance = weightedVADDistance(vad, preset)
        if distance < best_distance:
            best_distance = distance
            best_emotion = emotion
    return best_emotion if best_distance < 0.92 else "neutral"


# ── 情绪意图 ──────────────────────────────────────────────────────────────────


@dataclass
class EmotionIntent:
    """一次情绪推入意图。"""

    emotion: str
    intensity: float = 0.5
    variant: str | None = None
    naturalEmotion: str | None = None
    naturalVariant: str | None = None
    naturalVAD: dict[str, float] | None = None
    contextTags: list[str] = field(default_factory=list)
    sourceMessage: str | None = None


__all__ = [
    "VADVector",
    "neutralVAD",
    "emotionVADPresets",
    "getVADPreset",
    "clamp",
    "clamp01",
    "lerp",
    "seeded_random",
    "clampVAD",
    "lerpVAD",
    "magnitude",
    "weightedVADDistance",
    "nearestVADPreset",
    "EmotionIntent",
]
