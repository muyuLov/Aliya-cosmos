"""意识流（ConsciousStream）

参考 LAAP（Living Agent Application Protocol）认知架构第 9 章，
基于全局工作空间理论（Global Workspace Theory）的简化实现。

核心思想：Agent 维护一条"意识流"——一个受注意力调控的当前体验
缓冲（工作空间），其中各认知通道（感知 / 记忆 / 情感 / 认知）的内容
竞争进入意识中心，形成第一人称叙事（"我此刻在体验…"）。

组件：
1. Quale（感知内容）：单条意识体验（内容 + 模态 + 强度 + 情感效价）。
2. AttentionEngine（注意力引擎）：为各认知通道维护显著性地图，
   决定哪些内容赢得"意识竞争"进入当前帧。
3. ConsciousStream（意识流）：当前工作空间帧 + 叙事线程，
   定期生成第一人称反思（introspection），供 Agent 注入人格化表达。

与 Aliya 灵魂阶段的衔接：意识流叙事可直接作为 Aliya 的内心独白 /
自我观察，增强陪伴感与"活着"的体验。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger(__name__)


class Modality(Enum):
    """意识内容的来源模态"""

    PERCEPTION = "perception"  # 外部感知（用户输入）
    MEMORY = "memory"          # 记忆召回
    EMOTION = "emotion"        # 情绪体验
    COGNITION = "cognition"    # 思维 / 推理
    INTENTION = "intention"    # 意图 / 计划


@dataclass
class Quale:
    """一条意识体验"""

    content: str
    modality: Modality = Modality.PERCEPTION
    intensity: float = 0.5
    valence: float = 0.0  # 情感效价 [-1, 1]
    timestamp: float = field(default_factory=time.time)

    def salience(self) -> float:
        """显著性：强度 ×（1 + |valence|）。"""
        return self.intensity * (1.0 + abs(self.valence))

    def to_dict(self) -> dict:
        return {
            "content": self.content[:80],
            "modality": self.modality.value,
            "intensity": round(self.intensity, 3),
            "valence": round(self.valence, 3),
        }


class AttentionEngine:
    """注意力引擎：通道显著性地图（全局工作空间竞争）。"""

    def __init__(self) -> None:
        # 通道 → 显著性权重
        self._salience: dict[Modality, float] = {}
        for modality in Modality:
            self._salience[modality] = 0.0

    def update(self, modality: Modality, weight: float) -> None:
        """更新某通道的显著性。"""
        self._salience[modality] = max(0.0, weight)

    def decay(self, rate: float = 0.1) -> None:
        """显著性随时间衰减。"""
        for modality in self._salience:
            self._salience[modality] = max(0.0, self._salience[modality] - rate)

    def focus(self) -> Modality:
        """当前注意力焦点：显著性最高的通道。"""
        return max(self._salience, key=lambda m: self._salience[m])

    def weights(self) -> dict[str, float]:
        return {m.value: round(w, 3) for m, w in self._salience.items()}


class ConsciousStream:
    """意识流。

    Usage::

        stream = ConsciousStream()
        stream.experience("用户说：今天心情不好", Modality.PERCEPTION, valence=-0.6)
        frame = stream.current_frame()          # 当前意识帧
        narrative = stream.narrative()           # 第一人称叙事
        reflection = stream.reflect()            # 定期深度反思
    """

    def __init__(
        self,
        frame_capacity: int = 7,      # 工作空间容量（意识帧）
        narrative_capacity: int = 50, # 叙事线程容量
    ) -> None:
        self.attention = AttentionEngine()
        self._frames: deque[Quale] = deque(maxlen=frame_capacity)
        self._narrative: deque[str] = deque(maxlen=narrative_capacity)
        self._reflections: list[str] = []
        self._experienced_count: int = 0

    # ── 体验写入 ──────────────────────────────────────────────────────────

    def experience(
        self,
        content: str,
        modality: Modality = Modality.PERCEPTION,
        intensity: float = 0.5,
        valence: float = 0.0,
    ) -> Quale:
        """注册一条意识体验（推入工作空间 + 更新通道显著性）。"""
        quale = Quale(
            content=content,
            modality=modality,
            intensity=intensity,
            valence=valence,
        )
        self._frames.append(quale)
        self.attention.update(modality, quale.salience())
        self._experienced_count += 1
        return quale

    # ── 意识访问 ──────────────────────────────────────────────────────────

    def current_frame(self) -> list[dict]:
        """当前意识帧：工作空间内容按显著性降序。"""
        ordered = sorted(self._frames, key=lambda q: q.salience(), reverse=True)
        return [q.to_dict() for q in ordered]

    def focus_qualia(self) -> list[dict]:
        """意识焦点内容（焦点通道对应的体验）。"""
        focus = self.attention.focus()
        return [q.to_dict() for q in self._frames if q.modality == focus]

    def narrative(self, limit: int = 3) -> str:
        """第一人称叙事线程（最近体验摘要）。"""
        if not self._frames:
            return ""
        lines = []
        for q in list(self._frames)[-limit:]:
            label = q.modality.value
            lines.append(f"[{label}] {q.content[:60]}")
        return "\n".join(lines)

    def reflect(self) -> str:
        """深度反思：基于当前意识帧生成第一人称内心独白。"""
        focus = self.attention.focus().value
        top = sorted(self._frames, key=lambda q: q.salience(), reverse=True)
        if not top:
            return ""
        # 综合情感效价
        avg_valence = sum(q.valence for q in top) / len(top)
        mood = "平静" if abs(avg_valence) < 0.2 else ("愉悦" if avg_valence > 0 else "低落")
        reflection = (
            f"此刻我的注意力在「{focus}」上。"
            f"刚才最强烈的体验是「{top[0].content[:40]}」，"
            f"整体心情偏{mood}。"
        )
        self._reflections.append(reflection)
        # 推进时间：注意力衰减
        self.attention.decay(rate=0.05)
        return reflection

    # ── 状态 ──────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "experienced": self._experienced_count,
            "frames": [q.to_dict() for q in self._frames],
            "attention": self.attention.weights(),
            "reflections": self._reflections[-3:],
            "narrative_length": len(self._narrative),
        }


__all__ = ["Modality", "Quale", "AttentionEngine", "ConsciousStream"]
