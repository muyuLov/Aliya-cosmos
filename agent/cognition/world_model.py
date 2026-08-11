"""世界模型（WorldModel）

参考 LAAP（Living Agent Application Protocol）认知架构第 5 章。

世界模型维护一个实时更新的实体-关系图，Agent 通过它进行查询、
预测和反事实推理（简化版）。

数据结构：
- Entity：实体（对象 / 用户 / 工具 / 目标等），属性带置信度（Belief）。
- Relation：实体间关系（CAUSES / PREVENTS / CONTAINS / DEPENDS_ON /
  USES / IS_A / PART_OF 等），带置信度与证据。
- CausalLink：因果链接（condition → effect，带概率与置信度）。

核心能力：
- 贝叶斯信念更新：Belief.update() 用加权平均累积证据。
- 前向预测：predict() 沿因果链多步模拟，形成"心理演练"。
- 摘要输出：to_summary() 供 LLM 上下文注入。
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class EntityType(Enum):
    """实体类型"""

    CONCEPT = "concept"
    OBJECT = "object"
    AGENT = "agent"
    USER = "user"
    ACTION = "action"
    STATE = "state"
    EVENT = "event"
    TOOL = "tool"
    FILE = "file"
    GOAL = "goal"


class RelationType(Enum):
    """关系类型"""

    CAUSES = "causes"
    PREVENTS = "prevents"
    CONTAINS = "contains"
    DEPENDS_ON = "depends_on"
    USES = "uses"
    IS_A = "is_a"
    PART_OF = "part_of"
    RELATES_TO = "relates_to"
    PREFERS = "prefers"
    DISLIKES = "dislikes"


@dataclass
class Belief:
    """带置信度的属性值（贝叶斯信念）。

    Attributes:
        value: 属性值。
        confidence: 置信度 [0, 1]。
        evidence_count: 证据条数。
    """

    value: Any
    confidence: float = 0.5
    evidence_count: int = 1

    def update(self, new_value: Any, confidence: float) -> None:
        """贝叶斯加权平均更新。

        old_weight = evidence_count / (evidence_count + 1)
        new_weight = 1 / (evidence_count + 1)
        confidence = old_weight * confidence + new_weight * confidence
        """
        old_weight = self.evidence_count / (self.evidence_count + 1)
        new_weight = 1.0 / (self.evidence_count + 1)
        self.confidence = _clamp(
            old_weight * self.confidence + new_weight * confidence, 0.0, 1.0
        )
        # 高置信新证据覆盖值
        if confidence >= self.confidence or self.evidence_count == 0:
            self.value = new_value
        self.evidence_count += 1

    def to_dict(self) -> dict:
        return {"value": self.value, "confidence": round(self.confidence, 3)}


@dataclass
class Entity:
    """世界模型实体"""

    id: str
    name: str
    entity_type: EntityType
    properties: dict[str, Belief] = field(default_factory=dict)
    salience: float = 0.3  # 重要性 [0, 1]
    abstraction_level: int = 0  # 0=具体, 1=模式, 2=抽象
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type.value,
            "properties": {k: v.to_dict() for k, v in self.properties.items()},
            "salience": round(self.salience, 3),
        }


@dataclass
class Relation:
    """实体间关系"""

    source_id: str
    target_id: str
    relation_type: RelationType
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source_id,
            "target": self.target_id,
            "relation_type": self.relation_type.value,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class CausalLink:
    """因果链接（条件 → 效应）"""

    condition: str
    effect: str
    probability: float = 0.5
    confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "effect": self.effect,
            "probability": round(self.probability, 3),
            "confidence": round(self.confidence, 3),
        }


@dataclass
class SimulationResult:
    """前向预测结果"""

    steps: list[dict]
    final_state: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "steps": self.steps,
            "final_state": self.final_state,
            "confidence": round(self.confidence, 3),
        }


class WorldModel:
    """世界模型：实体-关系-因果图。

    Usage::

        wm = WorldModel()
        uid = wm.add_entity("Aliya", EntityType.AGENT)
        mem = wm.add_entity("记忆", EntityType.CONCEPT)
        wm.add_relation(uid, mem, RelationType.DEPENDS_ON)
        wm.add_causal_link("用户表达情绪", "Agent 共情回应", probability=0.8)
        sim = wm.predict("用户表达情绪")
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._name_index: dict[tuple[str, EntityType], Entity] = {}
        self._relations: list[Relation] = []
        self._causal_links: list[CausalLink] = []
        self._id_counter: int = 0

    # ── 实体管理 ──────────────────────────────────────────────────────────

    def add_entity(
        self,
        name: str,
        entity_type: EntityType | str = EntityType.CONCEPT,
        properties: dict[str, Any] | None = None,
        confidence: float = 0.5,
        salience: float = 0.3,
    ) -> str:
        """添加实体（同名同类型复用已存在实体）。

        Args:
            name: 实体名称。
            entity_type: 实体类型。
            properties: 属性字典（value → Belief 初始化）。
            confidence: 属性置信度。
            salience: 重要性。

        Returns:
            实体 ID。
        """
        if isinstance(entity_type, str):
            entity_type = EntityType(entity_type)
        existing = self._find_by_name(name, entity_type)
        if existing is not None:
            existing.salience = _clamp(existing.salience + 0.05, 0.0, 1.0)
            for key, value in (properties or {}).items():
                if key in existing.properties:
                    existing.properties[key].update(value, confidence)
                else:
                    existing.properties[key] = Belief(value, confidence)
            return existing.id

        self._id_counter += 1
        eid = f"e{self._id_counter}"
        entity = Entity(
            id=eid,
            name=name,
            entity_type=entity_type,
            salience=salience,
        )
        for key, value in (properties or {}).items():
            entity.properties[key] = Belief(value, confidence)
        self._entities[eid] = entity
        self._name_index[(name, entity_type)] = entity
        return eid

    def _find_by_name(self, name: str, entity_type: EntityType) -> Entity | None:
        return self._name_index.get((name, entity_type))

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def query_entities(
        self,
        entity_type: EntityType | None = None,
        min_salience: float = 0.0,
    ) -> list[Entity]:
        return [
            e for e in self._entities.values()
            if (entity_type is None or e.entity_type == entity_type)
            and e.salience >= min_salience
        ]

    # ── 关系管理 ──────────────────────────────────────────────────────────

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: RelationType | str,
        confidence: float = 0.5,
        evidence: str = "",
    ) -> None:
        if isinstance(relation_type, str):
            relation_type = RelationType(relation_type)
        self._relations.append(
            Relation(
                source_id=source_id,
                target_id=target_id,
                relation_type=relation_type,
                confidence=confidence,
                evidence=[evidence] if evidence else [],
            )
        )

    def find_related(
        self,
        entity_id: str,
        relation_types: list[RelationType] | None = None,
        limit: int = 10,
    ) -> list[tuple[Entity, Relation]]:
        """查询与某实体相关的所有实体（含方向）。"""
        results: list[tuple[Entity, Relation]] = []
        for rel in self._relations:
            if rel.source_id == entity_id or rel.target_id == entity_id:
                if relation_types and rel.relation_type not in relation_types:
                    continue
                other_id = rel.target_id if rel.source_id == entity_id else rel.source_id
                other = self._entities.get(other_id)
                if other:
                    results.append((other, rel))
        results.sort(key=lambda pair: pair[1].confidence, reverse=True)
        return results[:limit]

    # ── 因果与预测 ────────────────────────────────────────────────────────

    def add_causal_link(
        self,
        condition: str,
        effect: str,
        probability: float = 0.5,
        confidence: float = 0.5,
    ) -> None:
        self._causal_links.append(
            CausalLink(condition, effect, probability, confidence)
        )

    def predict(self, action: str, max_steps: int = 3) -> SimulationResult:
        """前向预测：沿因果链模拟行动的可能结果（心理演练）。

        Args:
            action: 行动描述。
            max_steps: 最大模拟步数。

        Returns:
            SimulationResult（步骤链 + 最终状态 + 置信度）。
        """
        steps: list[dict] = []
        current = action
        confidence = 1.0
        for _ in range(max_steps):
            matched = False
            for link in self._causal_links:
                if link.condition in current or current in link.condition:
                    probability = link.probability * link.confidence
                    steps.append({
                        "condition": link.condition,
                        "effect": link.effect,
                        "probability": round(probability, 3),
                    })
                    current = link.effect
                    confidence *= probability
                    matched = True
                    break
            if not matched:
                break
        return SimulationResult(steps=steps, final_state=current, confidence=confidence)

    # ── 摘要与统计 ────────────────────────────────────────────────────────

    def to_summary(self, limit: int = 10) -> str:
        """生成 LLM 可读的世界状态摘要。"""
        lines: list[str] = []
        entities = self.query_entities(min_salience=0.1)
        for entity in entities[:limit]:
            top_props = itertools.islice(entity.properties.items(), 4)
            props = ", ".join(
                f"{k}={v.value}({v.confidence:.2f})"
                for k, v in top_props
            )
            lines.append(f"- {entity.name}[{entity.entity_type.value}] {props}".rstrip())
        for rel in self._relations[:limit]:
            src = self._entities.get(rel.source_id)
            tgt = self._entities.get(rel.target_id)
            if src and tgt:
                lines.append(
                    f"- {src.name} --{rel.relation_type.value}--> {tgt.name} "
                    f"({rel.confidence:.2f})"
                )
        if self._causal_links:
            lines.append("[因果] " + "；".join(
                f"{c.condition}→{c.effect}({c.probability:.2f})"
                for c in self._causal_links[:5]
            ))
        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "entities": len(self._entities),
            "relations": len(self._relations),
            "causal_links": len(self._causal_links),
        }


__all__ = [
    "EntityType",
    "RelationType",
    "Belief",
    "Entity",
    "Relation",
    "CausalLink",
    "SimulationResult",
    "WorldModel",
]
