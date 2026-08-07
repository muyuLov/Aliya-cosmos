"""五层层次化记忆系统（HierarchicalMemory）

参考 LAAP（Living Agent Application Protocol）认知架构第 10 章。

五层记忆设计（各层不同表征 / 容量 / 遗忘曲线）：
1. 工作记忆（WorkingMemory）：容量 7±2（Miller's Law），当前对话活跃信息，
   高注意力权重保留，低权重被挤出。
2. 情景记忆（EpisodicMemory）：Agent"经历过"的事件（用户说了什么、调用了
   什么工具、结果如何），带重要性评分与 Ebbinghaus 遗忘。
3. 语义记忆（SemanticMemory）：学到的"事实"（用户偏好、环境信息、工具
   可靠性），带置信度，通过贝叶斯更新累积证据。
4. 程序记忆（ProceduralMemory）："如何做"的知识——可复用操作序列 / 技能
   模板，由巩固引擎从重复成功模式提取。
5. 向量记忆（VectorMemory）：嵌入层，语义相似度检索（复用 core.vector）。

跨层巩固机制（consolidation）：工作记忆 → 情景记忆 → 语义记忆 → 程序记忆。
触发条件：经验重复达到阈值（默认同主题 >= 3 次）或时间周期。
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

# 工作记忆容量（7 ± 2 → 取上限 9）
_WORKING_CAPACITY = 9
# 情景记忆最大保留事件数
_EPISODIC_CAPACITY = 1000
# 语义事实重复阈值：同主题重复 >= N 次触发巩固
_CONSOLIDATION_THRESHOLD = 3
# 遗忘基准半衰期（秒）：不重要记忆衰减快
_FORGET_HALF_LIFE = 60 * 60 * 24  # 1 天


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ── 各层数据结构 ─────────────────────────────────────────────────────────────


@dataclass
class WorkingChunk:
    """工作记忆块"""

    content: str
    attention_weight: float = 1.0
    created_at: float = field(default_factory=time.time)


@dataclass
class EpisodicRecord:
    """情景记忆事件"""

    content: str
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5
    context: str = ""  # 关联领域 / 来源


@dataclass
class SemanticFact:
    """语义记忆事实（带置信度）"""

    key: str
    value: str
    confidence: float = 0.5
    evidence_count: int = 1
    source: str = ""
    updated_at: float = field(default_factory=time.time)

    def update(self, new_value: str, confidence: float, source: str = "") -> None:
        """贝叶斯加权平均更新置信度。

        old_weight = evidence_count / (evidence_count + 1)
        new_weight = 1 / (evidence_count + 1)
        confidence = old_weight * confidence + new_weight * confidence
        """
        old_weight = self.evidence_count / (self.evidence_count + 1)
        new_weight = 1.0 / (self.evidence_count + 1)
        self.confidence = old_weight * self.confidence + new_weight * confidence
        # 高置信的新证据覆盖值
        if confidence >= self.confidence or self.evidence_count == 0:
            self.value = new_value
        self.evidence_count += 1
        self.source = source
        self.updated_at = time.time()


@dataclass
class SkillTemplate:
    """程序记忆技能模板"""

    name: str
    steps: list[str]
    domain: str = ""
    success_rate: float = 0.5
    used_count: int = 0


# ── 各层实现 ─────────────────────────────────────────────────────────────────


class WorkingMemory:
    """工作记忆：OrderedDict 实现注意力权重淘汰。"""

    def __init__(self, capacity: int = _WORKING_CAPACITY) -> None:
        self._chunks: OrderedDict[str, WorkingChunk] = OrderedDict()
        self._capacity = capacity

    def attend(self, content: str, weight: float = 1.0) -> None:
        """推入工作记忆块；已存在则提升权重并刷新。"""
        key = content.strip()
        if not key:
            return
        if key in self._chunks:
            chunk = self._chunks.pop(key)
            chunk.attention_weight = max(chunk.attention_weight, weight)
            chunk.created_at = time.time()
            self._chunks[key] = chunk
        else:
            self._chunks[key] = WorkingChunk(content=key, attention_weight=weight)
        # 容量超限：剔除注意力权重最低者
        while len(self._chunks) > self._capacity:
            self._evict_lowest()

    def _evict_lowest(self) -> None:
        if not self._chunks:
            return
        lowest_key = min(self._chunks, key=lambda k: self._chunks[k].attention_weight)
        del self._chunks[lowest_key]

    def recall(self, limit: int | None = None) -> list[str]:
        """按注意力权重降序返回内容。"""
        ordered = sorted(
            self._chunks.values(), key=lambda c: c.attention_weight, reverse=True
        )
        return [c.content for c in ordered[:limit]] if limit else [c.content for c in ordered]

    def recall_chunks(self, limit: int | None = None) -> list[WorkingChunk]:
        """按注意力权重降序返回工作记忆块对象（供巩固等内部逻辑使用）。"""
        ordered = sorted(
            self._chunks.values(), key=lambda c: c.attention_weight, reverse=True
        )
        return ordered[:limit] if limit else list(ordered)

    def __len__(self) -> int:
        return len(self._chunks)


class EpisodicMemory:
    """情景记忆：带重要性评分 + Ebbinghaus 遗忘。"""

    def __init__(self, capacity: int = _EPISODIC_CAPACITY) -> None:
        self._records: deque[EpisodicRecord] = deque(maxlen=capacity)

    def remember(
        self, content: str, importance: float = 0.5, context: str = ""
    ) -> None:
        self._records.append(
            EpisodicRecord(content=content, importance=importance, context=context)
        )

    def recall(self, limit: int = 5, min_importance: float = 0.0) -> list[EpisodicRecord]:
        """按重要性降序召回；应用遗忘曲线（重要性低的事件权重衰减）。"""
        now = time.time()
        scored: list[tuple[float, EpisodicRecord]] = []
        for rec in self._records:
            age = now - rec.timestamp
            forget_factor = _clamp(2.0 ** (-age / _FORGET_HALF_LIFE), 0.1, 1.0)
            score = rec.importance * forget_factor
            if score < min_importance:
                continue
            scored.append((score, rec))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [rec for _, rec in scored[:limit]]

    def __len__(self) -> int:
        return len(self._records)


class SemanticMemory:
    """语义记忆：事实 + 贝叶斯置信度。"""

    def __init__(self) -> None:
        self._facts: dict[str, SemanticFact] = {}

    def learn(self, key: str, value: str, confidence: float = 0.5, source: str = "") -> None:
        key = key.strip().lower()
        if not key:
            return
        if key in self._facts:
            self._facts[key].update(value, confidence, source)
        else:
            self._facts[key] = SemanticFact(
                key=key, value=value, confidence=confidence, source=source
            )

    def recall(self, key: str) -> SemanticFact | None:
        return self._facts.get(key.strip().lower())

    def search(self, query: str, limit: int = 5) -> list[SemanticFact]:
        """按关键词子串匹配 + 置信度排序。"""
        q = query.strip().lower()
        if not q:
            return []
        matches = [f for f in self._facts.values() if q in f.key or q in f.value.lower()]
        matches.sort(key=lambda f: f.confidence, reverse=True)
        return matches[:limit]

    def iter_all(self) -> list[SemanticFact]:
        return list(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)


class ProceduralMemory:
    """程序记忆：可复用技能模板。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillTemplate] = {}

    def learn(self, name: str, steps: list[str], domain: str = "", success_rate: float = 0.5) -> None:
        self._skills[name] = SkillTemplate(
            name=name, steps=list(steps), domain=domain, success_rate=success_rate
        )

    def recall(self, name: str) -> SkillTemplate | None:
        skill = self._skills.get(name)
        if skill:
            skill.used_count += 1
        return skill

    def skills_for_domain(self, domain: str) -> list[SkillTemplate]:
        return [s for s in self._skills.values() if s.domain == domain]

    def iter_all(self) -> list[SkillTemplate]:
        return list(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)


class VectorMemory:
    """向量记忆：嵌入语义检索（复用 core.vector，可选）。

    向量模块不可用时静默降级（enabled=False）。
    """

    def __init__(self) -> None:
        self._store: Any | None = None
        self.enabled: bool = False
        try:
            from core.vector.store import get_vector_store
            self._store = get_vector_store()
            self.enabled = True
        except Exception:
            logger.debug("[Memory] 向量记忆不可用（跳过）")

    async def add(self, text: str, metadata: dict | None = None) -> bool:
        if not self.enabled or self._store is None:
            return False
        try:
            await self._store.add(text, metadata=metadata)
            return True
        except Exception as e:
            logger.debug("[Memory] 向量记忆写入失败: %s", e)
            return False

    async def search(self, query: str, top_k: int = 3) -> list[str]:
        if not self.enabled or self._store is None:
            return []
        try:
            results = await self._store.search_async(query, top_k=top_k)
            return [r.text for r in results]
        except Exception as e:
            logger.debug("[Memory] 向量记忆检索失败: %s", e)
            return []


# ── 层级聚合 ─────────────────────────────────────────────────────────────────


class HierarchicalMemory:
    """五层记忆聚合器，提供统一写入 / 召回 / 巩固接口。

    Usage::

        mem = HierarchicalMemory()
        mem.attend("用户提到喜欢喝咖啡")       # 工作记忆
        mem.remember_episode("调用了查询工具", importance=0.8)  # 情景
        mem.learn_fact("用户偏好", "喜欢咖啡", confidence=0.8)   # 语义
        mem.consolidate()                       # 跨层巩固
        ctx = mem.build_context(limit=5)        # 组合召回 → 注入 LLM
    """

    def __init__(self, enable_vector: bool = True) -> None:
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self.vector = VectorMemory() if enable_vector else VectorMemory()
        if not enable_vector:
            self.vector.enabled = False

    # ── 写入 ──────────────────────────────────────────────────────────────

    def attend(self, content: str, weight: float = 1.0) -> None:
        """写入工作记忆。"""
        self.working.attend(content, weight=weight)

    def remember_episode(self, content: str, importance: float = 0.5, context: str = "") -> None:
        """写入情景记忆。"""
        self.episodic.remember(content, importance=importance, context=context)

    def learn_fact(self, key: str, value: str, confidence: float = 0.5, source: str = "") -> None:
        """写入语义记忆（贝叶斯更新）。"""
        self.semantic.learn(key, value, confidence=confidence, source=source)

    def learn_skill(self, name: str, steps: list[str], domain: str = "", success_rate: float = 0.5) -> None:
        """写入程序记忆（技能模板）。"""
        self.procedural.learn(name, steps, domain=domain, success_rate=success_rate)

    async def vector_add(self, text: str, metadata: dict | None = None) -> bool:
        """写入向量记忆。"""
        return await self.vector.add(text, metadata=metadata)

    # ── 召回 ──────────────────────────────────────────────────────────────

    def build_context(
        self, query: str = "", limit: int = 5, _include_vector: bool = True
    ) -> list[str]:
        """组合召回：语义 + 情景 + 工作记忆，构成可注入 LLM 的上下文。"""
        parts: list[str] = []
        for fact in self.semantic.search(query, limit=limit):
            parts.append(f"[记忆] {fact.key}: {fact.value}（置信 {fact.confidence:.2f}）")
        for rec in self.episodic.recall(limit=limit, min_importance=0.3):
            parts.append(f"[经历] {rec.content}")
        working = self.working.recall(limit=3)
        if working:
            parts.append("[当前] " + "；".join(working))
        return parts

    async def search_vector(self, query: str, top_k: int = 3) -> list[str]:
        return await self.vector.search(query, top_k=top_k)

    # ── 巩固机制 ──────────────────────────────────────────────────────────

    def consolidate(self) -> dict:
        """跨层巩固：工作记忆 → 情景记忆；重复事实提升置信。

        Returns:
            巩固动作统计 {"episodes": n, "facts": m}。
        """
        stats = {"episodes": 0, "facts": 0}

        # 工作记忆 → 情景记忆：高注意力块固化为情景
        for chunk in self.working.recall_chunks(limit=5):
            if chunk.attention_weight >= 0.7:
                self.episodic.remember(
                    chunk.content, importance=0.4, context="working→episodic"
                )
                stats["episodes"] += 1

        # 语义事实证据累积：低置信事实 → 高置信（重复出现的模式）
        for fact in self.semantic.iter_all():
            if fact.evidence_count >= _CONSOLIDATION_THRESHOLD and fact.confidence < 0.9:
                fact.confidence = min(0.9, fact.confidence + 0.1)
                stats["facts"] += 1

        return stats

    def get_stats(self) -> dict:
        return {
            "working": len(self.working),
            "episodic": len(self.episodic),
            "semantic": len(self.semantic),
            "procedural": len(self.procedural),
            "vector": "enabled" if self.vector.enabled else "disabled",
        }


__all__ = [
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "ProceduralMemory",
    "VectorMemory",
    "HierarchicalMemory",
]
