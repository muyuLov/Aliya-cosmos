"""五层层次化记忆系统（HierarchicalMemory）

参考 LAAP（Living Agent Application Protocol）认知架构第 10 章。

五层记忆设计（各层不同表征 / 容量 / 遗忘曲线 / 生命周期）：

1. 感知记忆（SensoryMemory）：原始输入的瞬态缓冲（默认 5 条 / 5 分钟窗口），
   到期自动丢弃，重要项由巩固机制冲刷进工作记忆。
2. 工作记忆（WorkingMemory）：容量 7±2（Miller's Law），当前对话活跃信息，
   高注意力权重保留，低权重被挤出；召回按注意力 × 时近度排序。
3. 情景记忆（EpisodicMemory）：Agent"经历过"的事件（用户说了什么、调用了
   什么工具、结果如何），带重要性评分与 Ebbinghaus 遗忘。
4. 语义记忆（SemanticMemory）：学到的"事实"（用户偏好、环境信息、工具
   可靠性），带置信度，通过贝叶斯更新累积证据。
5. 程序记忆（ProceduralMemory）："如何做"的知识——可复用操作序列 / 技能
   模板，由巩固引擎从重复成功模式提取。

元记忆（MetaMemory）：跨层使用统计（访问次数 / 命中率 / 关联热度），
驱动自适应更新（热度提升 / 长期未用降权）与检索排序。

跨层巩固机制（consolidation）：
  感知记忆 → 工作记忆 → 情景记忆 → 语义记忆 → 程序记忆。
触发条件：经验重复达到阈值（默认同主题 >= 3 次）或时间周期。

持久化：save() / load() 以 JSON 序列化各层（感知层为瞬态，不持久化），
支持跨会话恢复。

统一召回接口（recall / recall_async）：
  按层级优先级（工作 > 语义 > 情景 > 程序 > 感知）+ 上下文感知
  （查询关键词匹配 / 时近度 / 重要性 / 置信度）加权排序。
"""

from __future__ import annotations

import json
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
# 感知记忆容量与窗口（秒）
_SENSORY_CAPACITY = 5
_SENSORY_WINDOW = 300.0
# 元记忆容量
_META_CAPACITY = 2000
# 语义事实重复阈值：同主题重复 >= N 次触发巩固
_CONSOLIDATION_THRESHOLD = 3
# 遗忘基准半衰期（秒）：不重要记忆衰减快
_FORGET_HALF_LIFE = 60 * 60 * 24  # 1 天
# 工作记忆半衰期（秒）：会话内短期衰减
_WORKING_HALF_LIFE = 5 * 60  # 5 分钟
# 语义 / 程序记忆半衰期（秒）：长期记忆衰减慢
_LONG_TERM_HALF_LIFE = 7 * 24 * 60 * 60  # 7 天
# 自适应更新：超过此秒数未访问且热度低 → 降权
_ADAPT_INACTIVITY = 7 * 24 * 60 * 60  # 7 天
# 遗忘阈值：衰减后数值低于此值视为"被遗忘"，由 apply_forgetting 清理
_MEMORY_FORGET_THRESHOLD = 0.1
# 元记忆热度半衰期（秒）：热度随时间衰减
_META_HALF_LIFE = 30 * 24 * 60 * 60  # 30 天
# 层级优先级权重（recall 排序用）
_LAYER_PRIORITY = {
    "sensory": 0.6,
    "working": 1.0,
    "semantic": 0.95,
    "episodic": 0.85,
    "procedural": 0.7,
    "vector": 0.8,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _ebbinghaus_factor(age: float, half_life: float) -> float:
    """Ebbinghaus 遗忘曲线：age 达到 half_life 时衰减为 0.5。"""
    return _clamp(2.0 ** (-age / half_life), 0.0, 1.0)


# ── 各层数据结构 ─────────────────────────────────────────────────────────────


@dataclass
class WorkingChunk:
    """工作记忆块"""

    content: str
    attention_weight: float = 1.0
    created_at: float = field(default_factory=time.time)
    last_access_at: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self) -> None:
        self.last_access_at = time.time()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "attention_weight": self.attention_weight,
            "created_at": self.created_at,
            "last_access_at": self.last_access_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingChunk":
        return cls(
            content=data["content"],
            attention_weight=data.get("attention_weight", 1.0),
            created_at=data.get("created_at", time.time()),
            last_access_at=data.get("last_access_at", time.time()),
            access_count=data.get("access_count", 0),
        )


@dataclass
class EpisodicRecord:
    """情景记忆事件"""

    content: str
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5
    context: str = ""  # 关联领域 / 来源
    last_access_at: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self) -> None:
        self.last_access_at = time.time()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "timestamp": self.timestamp,
            "importance": self.importance,
            "context": self.context,
            "last_access_at": self.last_access_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EpisodicRecord":
        return cls(
            content=data["content"],
            timestamp=data.get("timestamp", time.time()),
            importance=data.get("importance", 0.5),
            context=data.get("context", ""),
            last_access_at=data.get("last_access_at", time.time()),
            access_count=data.get("access_count", 0),
        )


@dataclass
class SemanticFact:
    """语义记忆事实（带置信度）"""

    key: str
    value: str
    confidence: float = 0.5
    evidence_count: int = 1
    source: str = ""
    updated_at: float = field(default_factory=time.time)
    last_access_at: float = field(default_factory=time.time)
    access_count: int = 0

    def update(self, new_value: str, confidence: float, source: str = "") -> None:
        """贝叶斯加权平均更新置信度。

        old_weight = evidence_count / (evidence_count + 1)
        new_weight = 1 / (evidence_count + 1)
        new_confidence = old_weight * old_confidence + new_weight * new_confidence
        """
        old_confidence = self.confidence
        old_weight = self.evidence_count / (self.evidence_count + 1)
        new_weight = 1.0 / (self.evidence_count + 1)
        self.confidence = old_weight * old_confidence + new_weight * confidence
        self.evidence_count += 1
        # 高置信的新证据覆盖值（与旧置信度比较，而非更新后）
        if confidence >= old_confidence:
            self.value = new_value
        self.source = source
        self.updated_at = time.time()

    def boost(self, delta: float) -> None:
        """直接增加置信度（用于巩固机制），上限 1.0。"""
        self.confidence = min(self.confidence + delta, 1.0)

    def touch(self) -> None:
        self.last_access_at = time.time()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "source": self.source,
            "updated_at": self.updated_at,
            "last_access_at": self.last_access_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SemanticFact":
        return cls(
            key=data["key"],
            value=data["value"],
            confidence=data.get("confidence", 0.5),
            evidence_count=data.get("evidence_count", 1),
            source=data.get("source", ""),
            updated_at=data.get("updated_at", time.time()),
            last_access_at=data.get("last_access_at", time.time()),
            access_count=data.get("access_count", 0),
        )


@dataclass
class SkillTemplate:
    """程序记忆技能模板"""

    name: str
    steps: list[str]
    domain: str = ""
    success_rate: float = 0.5
    used_count: int = 0
    last_access_at: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self) -> None:
        self.last_access_at = time.time()
        self.access_count += 1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": list(self.steps),
            "domain": self.domain,
            "success_rate": self.success_rate,
            "used_count": self.used_count,
            "last_access_at": self.last_access_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillTemplate":
        return cls(
            name=data["name"],
            steps=list(data.get("steps", [])),
            domain=data.get("domain", ""),
            success_rate=data.get("success_rate", 0.5),
            used_count=data.get("used_count", 0),
            last_access_at=data.get("last_access_at", time.time()),
            access_count=data.get("access_count", 0),
        )


@dataclass
class SensoryItem:
    """感知记忆条目（瞬态）"""

    content: str
    weight: float = 1.0
    created_at: float = field(default_factory=time.time)
    channel: str = "user_input"  # 输入通道（用户 / 工具 / 环境）


@dataclass
class MetaRecord:
    """元记忆条目：跨层记忆使用统计"""

    item_key: str
    layer: str
    access_count: int = 0
    hit_count: int = 0
    heat: float = 0.0
    last_access_at: float = field(default_factory=time.time)

    @property
    def hit_rate(self) -> float:
        return self.hit_count / self.access_count if self.access_count else 0.0

    def touch(self, hit: bool = False) -> None:
        self.access_count += 1
        if hit:
            self.hit_count += 1
        self.heat = min(1.0, 0.85 * self.heat + (0.6 if hit else 0.15))
        self.last_access_at = time.time()

    def to_dict(self) -> dict:
        return {
            "item_key": self.item_key,
            "layer": self.layer,
            "access_count": self.access_count,
            "hit_count": self.hit_count,
            "heat": round(self.heat, 3),
            "last_access_at": self.last_access_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetaRecord":
        return cls(
            item_key=data["item_key"],
            layer=data["layer"],
            access_count=data.get("access_count", 0),
            hit_count=data.get("hit_count", 0),
            heat=data.get("heat", 0.0),
            last_access_at=data.get("last_access_at", time.time()),
        )


@dataclass
class MemoryHit:
    """统一召回结果"""

    layer: str  # sensory / working / episodic / semantic / procedural / vector
    content: str
    score: float
    item: Any | None = None
    meta: dict = field(default_factory=dict)

    def to_context(self, show_layer: bool = False) -> str:
        return f"[{self.layer}] {self.content}" if show_layer else self.content


# ── 各层实现 ─────────────────────────────────────────────────────────────────


class SensoryMemory:
    """感知记忆：原始输入瞬态缓冲。

    生命周期：短时窗口（默认 5 分钟）或容量上限；到期自动丢弃，
    重要项通过 flush 冲刷进工作记忆（由 consolidate 触发）。
    """

    def __init__(self, capacity: int = _SENSORY_CAPACITY, window: float = _SENSORY_WINDOW) -> None:
        self._items: deque[SensoryItem] = deque(maxlen=capacity)
        self._window = window

    def buffer(self, content: str, weight: float = 1.0, channel: str = "user_input") -> None:
        """写入感知缓冲。"""
        content = content.strip()
        if not content:
            return
        self._items.append(SensoryItem(content=content, weight=weight, channel=channel))

    def poll(self) -> None:
        """过期条目自动丢弃。"""
        now = time.time()
        while self._items and (now - self._items[0].created_at) > self._window:
            self._items.popleft()

    def flush(self, min_weight: float = 0.5) -> list[SensoryItem]:
        """冲刷：返回权重达标且未过期的条目并清空。"""
        self.poll()
        kept = [it for it in self._items if it.weight >= min_weight]
        self._items.clear()
        return kept

    def recall(self, limit: int = 3) -> list[SensoryItem]:
        """返回最新未过期条目（按权重降序）。"""
        self.poll()
        ordered = sorted(self._items, key=lambda it: it.weight, reverse=True)
        return ordered[:limit]

    def __len__(self) -> int:
        return len(self._items)


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
            chunk.last_access_at = time.time()
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

    def _score(self, chunk: WorkingChunk, now: float) -> float:
        """召回得分：注意力权重 × 时近度（Ebbinghaus）。"""
        freshness = _ebbinghaus_factor(now - chunk.created_at, _WORKING_HALF_LIFE)
        return chunk.attention_weight * (0.5 + 0.5 * freshness)

    def recall(self, limit: int | None = None) -> list[str]:
        """按注意力 × 时近度降序返回内容。"""
        ordered = self._recall_chunks_sorted(limit)
        return [c.content for c in ordered]

    def recall_chunks(self, limit: int | None = None) -> list[WorkingChunk]:
        """按得分降序返回工作记忆块对象（供巩固等内部逻辑使用）。"""
        return self._recall_chunks_sorted(limit)

    def _recall_chunks_sorted(self, limit: int | None = None) -> list[WorkingChunk]:
        now = time.time()
        ordered = sorted(
            self._chunks.values(), key=lambda c: self._score(c, now), reverse=True
        )
        return ordered[:limit] if limit else list(ordered)

    def __len__(self) -> int:
        return len(self._chunks)

    def to_dict(self) -> dict:
        return {
            "capacity": self._capacity,
            "chunks": [c.to_dict() for c in self._chunks.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingMemory":
        mem = cls(capacity=data.get("capacity", _WORKING_CAPACITY))
        for cd in data.get("chunks", []):
            chunk = WorkingChunk.from_dict(cd)
            mem._chunks[chunk.content] = chunk
        return mem


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
        """按重要性 × 遗忘曲线降序召回。"""
        now = time.time()
        scored: list[tuple[float, EpisodicRecord]] = []
        for rec in self._records:
            age = now - rec.timestamp
            forget_factor = _ebbinghaus_factor(age, _FORGET_HALF_LIFE)
            score = rec.importance * forget_factor
            if score < min_importance:
                continue
            scored.append((score, rec))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [rec for _, rec in scored[:limit]]

    def __len__(self) -> int:
        return len(self._records)

    def to_dict(self) -> dict:
        return {
            "capacity": self._records.maxlen,
            "records": [r.to_dict() for r in self._records],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EpisodicMemory":
        capacity = data.get("capacity") or _EPISODIC_CAPACITY
        mem = cls(capacity=capacity)
        for rd in data.get("records", []):
            mem._records.append(EpisodicRecord.from_dict(rd))
        return mem


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
        fact = self._facts.get(key.strip().lower())
        if fact:
            fact.touch()
        return fact

    def search(self, query: str, limit: int = 5) -> list[SemanticFact]:
        """按关键词子串匹配 + 置信度 × 时近度排序。"""
        q = query.strip().lower()
        if not q:
            return []
        now = time.time()
        matches = [f for f in self._facts.values() if q in f.key or q in f.value.lower()]
        matches.sort(
            key=lambda f: f.confidence * _ebbinghaus_factor(now - f.updated_at, _LONG_TERM_HALF_LIFE),
            reverse=True,
        )
        return matches[:limit]

    def iter_all(self):
        return self._facts.values()

    def __len__(self) -> int:
        return len(self._facts)

    def to_dict(self) -> dict:
        return {"facts": {k: f.to_dict() for k, f in self._facts.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "SemanticMemory":
        mem = cls()
        for key, fd in data.get("facts", {}).items():
            fact = SemanticFact.from_dict(fd)
            mem._facts[key] = fact
        return mem


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
            skill.touch()
        return skill

    def skills_for_domain(self, domain: str) -> list[SkillTemplate]:
        return [s for s in self._skills.values() if s.domain == domain]

    def iter_all(self):
        return self._skills.values()

    def __len__(self) -> int:
        return len(self._skills)

    def to_dict(self) -> dict:
        return {"skills": {k: s.to_dict() for k, s in self._skills.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "ProceduralMemory":
        mem = cls()
        for name, sd in data.get("skills", {}).items():
            mem._skills[name] = SkillTemplate.from_dict(sd)
        return mem


# VectorMemory 类已移除。向量存储现在作为 HierarchicalMemory 的内部跨层索引，
# 不再是一个独立的"记忆层"。长期记忆（语义/情景/程序）写入时自动同步到向量
# 存储，recall_async() 统一返回向量增强的召回结果。


class MetaMemory:
    """元记忆：跨层使用统计（自适应更新的依据）。"""

    def __init__(self, capacity: int = _META_CAPACITY) -> None:
        self._records: dict[str, MetaRecord] = {}
        self._capacity = capacity

    def _key(self, item_key: str, layer: str) -> str:
        return f"{layer}:{item_key}"

    def touch(self, item_key: str, layer: str, hit: bool = False) -> None:
        key = self._key(item_key, layer)
        rec = self._records.setdefault(key, MetaRecord(item_key=item_key, layer=layer))
        rec.touch(hit=hit)

    def get(self, item_key: str, layer: str) -> MetaRecord | None:
        return self._records.get(self._key(item_key, layer))

    def prune(self, active_keys: set[str]) -> None:
        """移除已不存在的记忆条目对应的元记录。"""
        for key in list(self._records):
            if key not in active_keys:
                del self._records[key]

    def _evict_if_needed(self) -> None:
        if len(self._records) <= self._capacity:
            return
        sorted_keys = sorted(self._records, key=lambda k: self._records[k].heat)
        for key in sorted_keys[: len(self._records) - self._capacity]:
            del self._records[key]

    def stats(self) -> dict:
        if not self._records:
            return {"count": 0}
        return {
            "count": len(self._records),
            "avg_hit_rate": round(
                sum(r.hit_rate for r in self._records.values()) / len(self._records), 3
            ),
            "avg_heat": round(
                sum(r.heat for r in self._records.values()) / len(self._records), 3
            ),
        }

    def to_dict(self) -> dict:
        return {
            "capacity": self._capacity,
            "records": [r.to_dict() for r in self._records.values()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetaMemory":
        mm = cls(capacity=data.get("capacity", _META_CAPACITY))
        for rd in data.get("records", []):
            rec = MetaRecord.from_dict(rd)
            mm._records[mm._key(rec.item_key, rec.layer)] = rec
        return mm


# ── 层级聚合 ─────────────────────────────────────────────────────────────────


class HierarchicalMemory:
    """五层记忆聚合器，提供统一写入 / 召回 / 巩固 / 持久化接口。

    Usage::

        mem = HierarchicalMemory()
        mem.buffer_sensory("用户提到喜欢喝咖啡")       # 感知记忆
        mem.attend("用户提到喜欢喝咖啡")               # 工作记忆
        mem.remember_episode("调用了查询工具", importance=0.8)  # 情景
        mem.learn_fact("用户偏好", "喜欢咖啡", confidence=0.8)   # 语义
        mem.consolidate()                              # 跨层巩固
        hits = mem.recall(query="咖啡", limit=5)       # 统一召回（按层级优先级）
        ctx = mem.build_context(limit=5)               # 组合召回 → 注入 LLM
        mem.save("data/memory/memory_state.json")      # 持久化
        mem2 = HierarchicalMemory.load("data/memory/memory_state.json")  # 恢复
    """

    def __init__(self, enable_vector: bool = True) -> None:
        self.sensory = SensoryMemory()
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.procedural = ProceduralMemory()
        self.meta = MetaMemory()
        self._stats: dict[str, int] = {"recalls": 0, "hits": 0}
        # 向量存储作为跨层语义索引（非独立记忆层），延迟加载
        self._vector_store: Any = None
        self._vector_enabled: bool = enable_vector
        self._vector_ready: bool = False  # 实际初始化成功标记
        self._pending_sync: list[tuple[str, dict]] = []  # 待同步队列（同步写→异步同步桥接）

    def _ensure_vector_store(self) -> Any:
        """延迟初始化向量存储（单例获取，失败静默降级）。"""
        if not self._vector_enabled or self._vector_ready:
            return self._vector_store
        try:
            from core.vector.store import get_vector_store
            self._vector_store = get_vector_store()
            self._vector_ready = True
        except Exception as e:
            self._vector_enabled = False
            logger.debug("[Memory] 向量存储不可用（降级纯内存）: %s", e)
        return self._vector_store

    def _sync_to_vector(self, text: str, metadata: dict | None = None) -> None:
        """非阻塞排入向量同步队列（同步写入调用→异步批量持久化）。"""
        store = self._ensure_vector_store()
        if store is None or not text.strip():
            return
        self._pending_sync.append((text, metadata or {}))

    async def _drain_sync(self) -> None:
        """消费待同步队列，批量写入向量存储。在 recall_async 开头调用。"""
        store = self._ensure_vector_store()
        if store is None or not self._pending_sync:
            return
        batch = self._pending_sync[:]
        self._pending_sync.clear()
        for text, metadata in batch:
            try:
                await store.add(text, metadata=metadata)
            except Exception as e:
                logger.debug("[Memory] 向量同步失败: %s", e)

    # ── 写入 ──────────────────────────────────────────────────────────────

    def buffer_sensory(self, content: str, weight: float = 1.0, channel: str = "user_input") -> None:
        """写入感知记忆（瞬态缓冲）。"""
        self.sensory.buffer(content, weight=weight, channel=channel)

    def attend(self, content: str, weight: float = 1.0) -> None:
        """写入工作记忆。"""
        self.working.attend(content, weight=weight)

    def remember_episode(self, content: str, importance: float = 0.5, context: str = "") -> None:
        """写入情景记忆 → 自动同步到向量索引。"""
        self.episodic.remember(content, importance=importance, context=context)
        if importance >= 0.3:  # 低重要性不浪费向量空间
            self._sync_to_vector(content, metadata={"layer": "episodic", "context": context})

    def learn_fact(self, key: str, value: str, confidence: float = 0.5, source: str = "") -> None:
        """写入语义记忆 → 自动同步到向量索引。"""
        self.semantic.learn(key, value, confidence=confidence, source=source)
        self._sync_to_vector(f"{key}: {value}", metadata={"layer": "semantic", "key": key, "source": source})

    def learn_skill(self, name: str, steps: list[str], domain: str = "", success_rate: float = 0.5) -> None:
        """写入程序记忆 → 自动同步到向量索引。"""
        self.procedural.learn(name, steps, domain=domain, success_rate=success_rate)
        text = f"{name}: {'→'.join(steps)}" if steps else name
        # metadata 携带 name，供遗忘时精确删除对应向量条目
        self._sync_to_vector(text, metadata={"layer": "procedural", "domain": domain, "name": name})

    # ── 统一召回 ──────────────────────────────────────────────────────────

    @staticmethod
    def _item_key(hit: "MemoryHit") -> str:
        """获取条目的元记忆键（语义用 key，其余用 content）。"""
        if hit.layer == "semantic" and isinstance(hit.item, SemanticFact):
            return hit.item.key
        if hit.item is not None:
            return getattr(hit.item, "content", hit.content)
        return hit.content

    def recall(
        self,
        query: str = "",
        limit: int = 5,
        context: dict | None = None,
    ) -> list[MemoryHit]:
        """统一召回：按层级优先级 + 上下文感知加权排序。

        层级优先级：工作 > 语义 > 情景 > 程序 > 感知。
        上下文感知：查询关键词匹配、时近度、重要性 / 置信度 / 成功率。

        Args:
            query: 查询文本（用于关键词匹配加权）。
            limit: 返回条目上限。
            context: 上下文提示（可选），支持 ``topic`` / ``domain`` 字段
                提升程序记忆与情景记忆的领域相关性。

        Returns:
            按综合得分降序的 MemoryHit 列表。
        """
        context = context or {}
        q = (query or "").strip().lower()
        hits: list[MemoryHit] = []

        # 感知层：缓冲条目（query 匹配才纳入）
        for it in self.sensory.recall(limit=limit):
            if q and q not in it.content.lower():
                continue
            hits.append(
                MemoryHit(
                    layer="sensory",
                    content=it.content,
                    score=_clamp(it.weight, 0.0, 1.0) * _LAYER_PRIORITY["sensory"],
                    meta={"channel": it.channel},
                )
            )

        # 工作层：注意力 × 时近度
        for chunk in self.working.recall_chunks(limit=limit):
            hits.append(
                MemoryHit(
                    layer="working",
                    content=chunk.content,
                    score=_clamp(chunk.attention_weight, 0.0, 1.0) * _LAYER_PRIORITY["working"],
                    item=chunk,
                )
            )

        # 语义层：事实（置信度 × 时近度，关键词匹配）
        for fact in self.semantic.search(query, limit=limit):
            score = fact.confidence * _LAYER_PRIORITY["semantic"]
            hits.append(
                MemoryHit(
                    layer="semantic",
                    content=f"{fact.key}: {fact.value}",
                    score=score,
                    item=fact,
                )
            )

        # 情景层：重要性 × 遗忘曲线；弱相关降权但不剔除
        for rec in self.episodic.recall(limit=limit, min_importance=0.0):
            base = _clamp(rec.importance, 0.0, 1.0) * _LAYER_PRIORITY["episodic"]
            if q and q not in rec.content.lower():
                base *= 0.6
            hits.append(
                MemoryHit(
                    layer="episodic",
                    content=rec.content,
                    score=base,
                    item=rec,
                )
            )

        # 程序层：领域相关优先
        domain = context.get("domain") or context.get("topic")
        skills = (
            self.procedural.skills_for_domain(domain)
            if domain
            else list(self.procedural.iter_all())
        )
        for skill in skills[:limit]:
            content = f"{skill.name}: {'→'.join(skill.steps)}" if skill.steps else skill.name
            hits.append(
                MemoryHit(
                    layer="procedural",
                    content=content,
                    score=_clamp(skill.success_rate, 0.0, 1.0) * _LAYER_PRIORITY["procedural"],
                    item=skill,
                )
            )

        # 综合排序
        hits.sort(key=lambda h: h.score, reverse=True)
        kept = hits[:limit]

        # 元记忆自适应更新：命中条目 touch（热度 / 命中率）
        self._stats["recalls"] += 1
        for h in kept:
            if h.item is not None and hasattr(h.item, "touch"):
                h.item.touch()
            self.meta.touch(self._item_key(h), h.layer, hit=True)
        self._stats["hits"] += len(kept)
        return kept

    async def recall_async(
        self,
        query: str = "",
        limit: int = 5,
        context: dict | None = None,
    ) -> list[MemoryHit]:
        """统一召回（异步版）：先排空向量同步队列，再执行各层召回 + 向量语义增强。"""
        # 排空待同步队列：保证最新写入内容可被向量检索命中
        await self._drain_sync()
        hits = self.recall(query, limit=limit, context=context)
        store = self._ensure_vector_store()
        if store is not None and query:
            try:
                results = await store.search_async(query, top_k=limit)
                existing = {h.content for h in hits}
                for r in results:
                    text = r.text if hasattr(r, "text") else str(r)
                    if text not in existing:
                        hits.append(
                            MemoryHit(
                                layer="vector",
                                content=text,
                                score=0.75 * _LAYER_PRIORITY["vector"],
                                meta=getattr(r, "metadata", {}) if hasattr(r, "metadata") else {},
                            )
                        )
            except Exception as e:
                logger.debug("[Memory] 向量召回失败（忽略）: %s", e)
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    # ── 上下文构建 ────────────────────────────────────────────────────────

    @staticmethod
    def _format_hit(h: "MemoryHit") -> str:
        """将单个 MemoryHit 格式化为 LLM 可注入文本。"""
        if h.layer == "working":
            return f"[当前] {h.content}"
        elif h.layer == "semantic":
            return f"[记忆] {h.content}（置信 {getattr(h.item, 'confidence', 0.0):.2f}）"
        elif h.layer == "episodic":
            return f"[经历] {h.content}"
        elif h.layer == "procedural":
            return f"[技能] {h.content}"
        elif h.layer == "sensory":
            return f"[感知] {h.content}"
        elif h.layer == "vector":
            return f"[关联] {h.content}"
        return h.content

    def _hits_to_context(self, hits: list[MemoryHit]) -> list[str]:
        """MemoryHit 列表 → LLM 上下文文本列表。"""
        return [self._format_hit(h) for h in hits]

    def build_context(self, query: str = "", limit: int = 5) -> list[str]:
        """同步版上下文构建（仅各层规则召回，不含向量语义增强）。

        如需向量增强，请使用 build_context_async()。
        """
        return self._hits_to_context(self.recall(query=query, limit=limit))

    async def build_context_async(self, query: str = "", limit: int = 5) -> list[str]:
        """异步版上下文构建：含向量语义增强的完整召回。"""
        return self._hits_to_context(await self.recall_async(query=query, limit=limit))

    # ── 实体记忆属性聚合（供图节点挂载）────────────────────────────────────

    def collect_entity_memory_attrs(self, entity_name: str) -> dict:
        """聚合实体相关的五层记忆属性，供挂载到图数据库 Entity 节点。

        图节点以五元组承载实体关系，本身不含记忆层信息；此方法把五层
        层次化记忆中与该实体相关条目的层信息 / 重要性 / 置信度等聚合成
        属性字典，由 graph.store_quintuples 的 ``memory_attrs_by_entity``
        参数写入节点，使图记忆同时反映五层记忆的状态。

        Returns:
            形如 ``{"layers": "episodic;semantic", "importance": 0.8, ...}``
            的属性字典；实体未命中任何记忆层时返回空 dict。
        """
        name = (entity_name or "").strip()
        if not name:
            return {}
        lower = name.lower()

        layers: set[str] = set()
        importance = 0.0        # 情景层
        confidence = 0.0        # 语义层
        success_rate = 0.0      # 程序层
        attention_weight = 0.0  # 工作层
        access_count = 0        # 各层访问计数
        heat = 0.0              # 元记忆热度

        # 工作层：内容包含实体的记忆块
        for chunk in self.working.recall_chunks():
            if lower in chunk.content.lower() or chunk.content.lower() in lower:
                layers.add("working")
                attention_weight = max(attention_weight, chunk.attention_weight)
                access_count += chunk.access_count

        # 情景层：内容包含实体的事件
        for rec in self.episodic.recall(limit=_EPISODIC_CAPACITY):
            if lower in rec.content.lower():
                layers.add("episodic")
                importance = max(importance, rec.importance)
                access_count += rec.access_count

        # 语义层：key / value 匹配的事实
        for fact in self.semantic.search(name, limit=50):
            layers.add("semantic")
            confidence = max(confidence, fact.confidence)
            access_count += fact.access_count

        # 程序层：名称 / 领域匹配的技能
        for skill in self.procedural.iter_all():
            if lower in skill.name.lower() or lower in skill.domain.lower():
                layers.add("procedural")
                success_rate = max(success_rate, skill.success_rate)
                access_count += skill.access_count

        # 元记忆：热度取各命中层中的最大值
        for layer in layers:
            rec = self.meta.get(name, layer)
            if rec is not None:
                heat = max(heat, rec.heat)

        if not layers:
            return {}
        return {
            "layers": ";".join(sorted(layers)),
            "importance": round(importance, 3),
            "confidence": round(confidence, 3),
            "success_rate": round(success_rate, 3),
            "attention_weight": round(attention_weight, 3),
            "access_count": access_count,
            "heat": round(heat, 3),
        }

    # ── 遗忘机制 ──────────────────────────────────────────────────────────

    def apply_forgetting(self) -> dict:
        """统一遗忘：各层数值按 Ebbinghaus 曲线随未访问时长永久衰减。

        召回（recall）阶段的 Ebbinghaus 因子只在排序时临时生效，记忆数值
        本身不衰减；此方法执行真正的遗忘：
          - 情景记忆：importance 按基准半衰期（1 天）衰减；
          - 语义记忆：confidence 按长期半衰期（7 天）衰减；
          - 程序记忆：success_rate 按长期半衰期（7 天）衰减；
          - 元记忆：heat 按长期半衰期（30 天）衰减；
        衰减后数值低于 ``_MEMORY_FORGET_THRESHOLD`` 的条目被移除。

        Returns:
            遗忘动作统计（各层衰减 / 清理计数 + 向量索引清理数）。
        """
        now = time.time()
        stats = {
            "episodic_decayed": 0,
            "episodic_forgotten": 0,
            "semantic_decayed": 0,
            "semantic_forgotten": 0,
            "procedural_decayed": 0,
            "procedural_forgotten": 0,
            "meta_decayed": 0,
            "meta_forgotten": 0,
            "vector_purged": 0,
        }
        # 被遗忘条目标识：(layer, content_or_key)，用于向量索引联动清理
        forgotten_items: list[tuple[str, str]] = []

        # 情景层：importance 衰减 + 清理被遗忘事件
        for rec in list(self.episodic._records):
            age = max(0.0, now - rec.last_access_at)
            rec.importance *= _ebbinghaus_factor(age, _FORGET_HALF_LIFE)
            stats["episodic_decayed"] += 1
            if rec.importance < _MEMORY_FORGET_THRESHOLD:
                self.episodic._records.remove(rec)
                stats["episodic_forgotten"] += 1
                forgotten_items.append(("episodic", rec.content))

        # 语义层：confidence 衰减 + 清理被遗忘事实
        for fact in list(self.semantic.iter_all()):
            age = max(0.0, now - fact.last_access_at)
            fact.confidence *= _ebbinghaus_factor(age, _LONG_TERM_HALF_LIFE)
            stats["semantic_decayed"] += 1
            if fact.confidence < _MEMORY_FORGET_THRESHOLD:
                del self.semantic._facts[fact.key]
                stats["semantic_forgotten"] += 1
                forgotten_items.append(("semantic", fact.key))

        # 程序层：success_rate 衰减 + 清理被遗忘技能
        for skill in list(self.procedural.iter_all()):
            age = max(0.0, now - skill.last_access_at)
            skill.success_rate *= _ebbinghaus_factor(age, _LONG_TERM_HALF_LIFE)
            stats["procedural_decayed"] += 1
            if skill.success_rate < _MEMORY_FORGET_THRESHOLD:
                del self.procedural._skills[skill.name]
                stats["procedural_forgotten"] += 1
                forgotten_items.append(("procedural", skill.name))

        # 元记忆：heat 衰减 + 清理
        for key in list(self.meta._records):
            rec = self.meta._records[key]
            age = max(0.0, now - rec.last_access_at)
            rec.heat *= _ebbinghaus_factor(age, _META_HALF_LIFE)
            stats["meta_decayed"] += 1
            if rec.heat < _MEMORY_FORGET_THRESHOLD:
                del self.meta._records[key]
                stats["meta_forgotten"] += 1

        # 元记忆修剪：清理已消失条目的元记录
        self.meta.prune(self._collect_active_keys())
        # 向量索引联动：删除被遗忘条目，避免"幽灵记忆"仍能被向量召回
        stats["vector_purged"] = self._purge_vector_forgotten(forgotten_items)
        return stats

    def _purge_vector_forgotten(self, items: list[tuple[str, str]]) -> int:
        """从向量索引移除被遗忘条目，返回清理数。

        匹配策略（与 _sync_to_vector 的 metadata/text 一一对应）：
          - semantic:    metadata ``layer=semantic`` + ``key=条目key``；
          - procedural:  metadata ``layer=procedural`` + ``name=条目名``；
          - episodic:    文本精确等于事件内容（metadata 无内容标识）。

        同步遍历内存存储删除（低频维护操作，可接受）；同时清理
        ``_pending_sync`` 中尚未入库的匹配文本，避免"写入后被遗忘却仍入库"。
        """
        if not items:
            return 0
        store = self._ensure_vector_store()
        if store is None:
            return 0

        purged = 0
        # 情景/程序层需从 _pending_sync 中移除未入库文本
        pending_filtered: list[tuple[str, dict]] = []
        for text, metadata in self._pending_sync:
            layer = metadata.get("layer")
            match = (
                layer == "episodic"
                and any(text == it[1] for it in items if it[0] == "episodic")
            ) or (
                layer == "procedural"
                and any(metadata.get("name") == it[1] for it in items if it[0] == "procedural")
            ) or (
                layer == "semantic"
                and any(metadata.get("key") == it[1] for it in items if it[0] == "semantic")
            )
            if match:
                purged += 1  # 尚未入库，从队列移除即视为清理
            else:
                pending_filtered.append((text, metadata))
        self._pending_sync = pending_filtered

        for layer, content_or_key in items:
            try:
                if layer == "semantic":
                    ids = store.find_ids(metadata={"layer": "semantic", "key": content_or_key})
                elif layer == "procedural":
                    ids = store.find_ids(metadata={"layer": "procedural", "name": content_or_key})
                else:  # episodic
                    ids = store.find_ids(text=content_or_key)
                if ids:
                    purged += store.delete_many(ids)
            except Exception as e:
                logger.debug("[Memory] 遗忘清理向量条目失败(%s): %s", content_or_key, e)
        return purged

    # ── 巩固机制 ──────────────────────────────────────────────────────────

    def consolidate(self) -> dict:
        """跨层巩固：感知→工作→情景→语义 + 元记忆修剪与自适应更新。

        Returns:
            巩固动作统计（兼容键 episodes / facts + 新增键）。
        """
        stats = {
            "sensory_to_working": 0,
            "episodes": 0,
            "episodic_to_semantic": 0,
            "facts": 0,
        }

        # 感知 → 工作：重要感知冲刷进工作记忆
        for it in self.sensory.flush(min_weight=0.5):
            self.working.attend(it.content, weight=it.weight)
            stats["sensory_to_working"] += 1

        # 工作记忆 → 情景记忆：高注意力块固化为情景
        for chunk in self.working.recall_chunks(limit=5):
            if chunk.attention_weight >= 0.7:
                self.episodic.remember(
                    chunk.content, importance=0.4, context="working→episodic"
                )
                stats["episodes"] += 1

        # 情景 → 语义：相同内容重复 >= 阈值固化为事实（经 learn_fact 自动向量同步）
        counts: dict[str, int] = {}
        for rec in self.episodic._records:
            counts[rec.content] = counts.get(rec.content, 0) + 1
        for content, count in counts.items():
            if count >= _CONSOLIDATION_THRESHOLD:
                self.learn_fact(content[:30], content, confidence=0.6, source="episodic")
                stats["episodic_to_semantic"] += 1

        # 语义事实证据累积：低置信事实 → 高置信（重复出现的模式）
        for fact in self.semantic.iter_all():
            if fact.evidence_count >= _CONSOLIDATION_THRESHOLD and fact.confidence < 0.9:
                fact.boost(0.1)
                stats["facts"] += 1

        # 自适应更新 + 元记忆修剪
        self._adapt()
        self.meta.prune(self._collect_active_keys())
        return stats

    def _adapt(self) -> None:
        """自适应更新：热度高条目提升重要性，长期未访问且低热度条目降权。"""
        now = time.time()
        for rec in list(self.episodic._records):
            meta_rec = self.meta.get(rec.content, "episodic")
            if meta_rec is None:
                continue
            if meta_rec.heat >= 0.5:
                rec.importance = min(1.0, rec.importance + 0.03)
            if now - rec.last_access_at > _ADAPT_INACTIVITY and meta_rec.heat < 0.2:
                rec.importance = max(0.0, rec.importance - 0.05)
        for fact in list(self.semantic.iter_all()):
            meta_rec = self.meta.get(fact.key, "semantic")
            if meta_rec is None:
                continue
            if now - fact.last_access_at > _ADAPT_INACTIVITY and meta_rec.heat < 0.2:
                fact.confidence = max(0.0, fact.confidence - 0.03)

    def _collect_active_keys(self) -> set[str]:
        """收集当前所有层的活跃条目键（供元记忆修剪）。"""
        keys: set[str] = set()
        for chunk in self.working._chunks.values():
            keys.add(f"working:{chunk.content}")
        for rec in self.episodic._records:
            keys.add(f"episodic:{rec.content}")
        for fact in self.semantic.iter_all():
            keys.add(f"semantic:{fact.key}")
        for skill in self.procedural.iter_all():
            keys.add(f"procedural:{skill.name}")
        return keys

    # ── 持久化 ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为 JSON 可编码字典。

        感知 / 工作记忆为会话级瞬态层，不持久化；
        长期记忆（情景 / 语义 / 程序）与元记忆参与跨会话恢复。
        """
        return {
            "episodic": self.episodic.to_dict(),
            "semantic": self.semantic.to_dict(),
            "procedural": self.procedural.to_dict(),
            "meta": self.meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HierarchicalMemory":
        """从字典恢复（工作 / 感知记忆从空会话开始，向量层重新初始化）。"""
        mem = cls(enable_vector=True)
        if "episodic" in data:
            mem.episodic = EpisodicMemory.from_dict(data["episodic"])
        if "semantic" in data:
            mem.semantic = SemanticMemory.from_dict(data["semantic"])
        if "procedural" in data:
            mem.procedural = ProceduralMemory.from_dict(data["procedural"])
        if "meta" in data:
            mem.meta = MetaMemory.from_dict(data["meta"])
        return mem

    def save(self, path: str) -> bool:
        """持久化全部长期记忆层到 JSON 文件。"""
        try:
            import os
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.warning("[Memory] 持久化失败: %s", e)
            return False

    @classmethod
    def load(cls, path: str) -> "HierarchicalMemory | None":
        """从 JSON 文件恢复记忆（跨会话恢复）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.warning("[Memory] 记忆恢复失败: %s", e)
            return None

    # ── 状态 ──────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "sensory": len(self.sensory),
            "working": len(self.working),
            "episodic": len(self.episodic),
            "semantic": len(self.semantic),
            "procedural": len(self.procedural),
            "vector": "ready" if self._vector_ready else ("enabled" if self._vector_enabled else "disabled"),
            "meta": self.meta.stats(),
            "recalls": self._stats["recalls"],
            "hits": self._stats["hits"],
        }


__all__ = [
    "WorkingChunk",
    "EpisodicRecord",
    "SemanticFact",
    "SkillTemplate",
    "SensoryItem",
    "MetaRecord",
    "MemoryHit",
    "SensoryMemory",
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "ProceduralMemory",
    "MetaMemory",
    "HierarchicalMemory",
]
