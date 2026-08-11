"""类比迁移引擎（AnalogicalEngine）

参考 LAAP（Living Agent Application Protocol）认知架构第 6.3 节，
实现结构映射（Structural Mapping）类比的简化版本。

核心思想：类比不是表面的特征相似，而是"关系的结构同构"。
Agent 把经历编码为结构图（节点 + 关系），抽象为关系骨架
（忽略表面属性），再与其他领域的骨架对齐——如果结构一致，
就能把一个领域学到的策略迁移到另一个领域。

管道（对应 LAAP analogical_pipeline）：
1. 编码（Encode）：将领域经验编码为结构图。
2. 抽象（Abstract）：提取关系骨架（关系三元组，忽略节点表面特征）。
3. 对齐（Align）：两个图的关系骨架匹配，计算结构相似度。
4. 投射（Project）：把源领域的策略节点投射到目标领域。
5. 评估（Evaluate）：评估迁移建议的置信度。

预定义抽象模式（对应 LAAP 的 pattern templates）：
- debugging：根因定位（症状 → 原因 → 修复）
- optimization：调优（瓶颈 → 参数 → 改进）
- negotiation：协商（分歧 → 利益 → 折中）
- exploration：探索（未知 → 假设 → 验证）

与持续学习的衔接：类比引擎可从学习管道的策略库 / 自我模型的
技能档案提取领域结构，实现"跨领域经验迁移"。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger(__name__)


class NodeType(Enum):
    """结构图节点类型"""

    PROBLEM = "problem"        # 问题 / 症状
    CAUSE = "cause"            # 原因
    SOLUTION = "solution"      # 解决方案 / 策略
    GOAL = "goal"              # 目标
    CONSTRAIN = "constraint"   # 约束
    TOOL = "tool"              # 工具 / 手段


class RelType(Enum):
    """结构图关系类型"""

    CAUSES = "causes"              # 导致
    MITIGATES = "mitigates"        # 缓解 / 修复
    REQUIRES = "requires"          # 需要
    BLOCKS = "blocks"              # 阻碍
    ENABLES = "enables"            # 促成
    PART_OF = "part_of"            # 属于


@dataclass
class Node:
    """结构图节点"""

    label: str
    node_type: NodeType = NodeType.PROBLEM

    def to_dict(self) -> dict:
        return {"label": self.label, "type": self.node_type.value}


@dataclass
class Relation:
    """结构图关系"""

    source: str
    target: str
    rel_type: RelType = RelType.CAUSES

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "rel": self.rel_type.value}


@dataclass
class StructuralGraph:
    """领域结构图"""

    domain: str = ""
    nodes: dict[str, Node] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    def add_node(self, label: str, node_type: NodeType = NodeType.PROBLEM) -> None:
        if label not in self.nodes:
            self.nodes[label] = Node(label=label, node_type=node_type)

    def add_relation(
        self,
        source: str,
        target: str,
        rel_type: RelType = RelType.CAUSES,
        source_type: NodeType = NodeType.PROBLEM,
        target_type: NodeType = NodeType.PROBLEM,
    ) -> None:
        self.add_node(source, source_type)
        self.add_node(target, target_type)
        self.relations.append(Relation(source=source, target=target, rel_type=rel_type))

    # ── 抽象（关系骨架） ──────────────────────────────────────────────────

    def abstract(self) -> list[tuple[NodeType, RelType, NodeType]]:
        """抽取关系骨架：忽略节点名称，只保留（类型, 关系, 类型）三元组。"""
        skeleton: list[tuple[NodeType, RelType, NodeType]] = []
        for rel in self.relations:
            src = self.nodes.get(rel.source)
            tgt = self.nodes.get(rel.target)
            if src and tgt:
                skeleton.append((src.node_type, rel.rel_type, tgt.node_type))
        return skeleton

    def node_count(self) -> int:
        return len(self.nodes)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "relations": [r.to_dict() for r in self.relations],
        }


# ── 预定义抽象模式（结构模板） ───────────────────────────────────────────────

_PATTERN_TEMPLATES: dict[str, list[tuple[NodeType, RelType, NodeType]]] = {
    "debugging": [
        (NodeType.PROBLEM, RelType.CAUSES, NodeType.CAUSE),
        (NodeType.CAUSE, RelType.REQUIRES, NodeType.TOOL),
        (NodeType.TOOL, RelType.MITIGATES, NodeType.PROBLEM),
    ],
    "optimization": [
        (NodeType.PROBLEM, RelType.BLOCKS, NodeType.GOAL),
        (NodeType.TOOL, RelType.ENABLES, NodeType.GOAL),
        (NodeType.TOOL, RelType.MITIGATES, NodeType.PROBLEM),
    ],
    "negotiation": [
        (NodeType.PROBLEM, RelType.BLOCKS, NodeType.GOAL),
        (NodeType.CAUSE, RelType.REQUIRES, NodeType.CONSTRAIN),
        (NodeType.SOLUTION, RelType.MITIGATES, NodeType.PROBLEM),
    ],
    "exploration": [
        (NodeType.PROBLEM, RelType.REQUIRES, NodeType.TOOL),
        (NodeType.TOOL, RelType.ENABLES, NodeType.GOAL),
        (NodeType.SOLUTION, RelType.CAUSES, NodeType.GOAL),
    ],
}


class AnalogicalEngine:
    """类比迁移引擎。

    Usage::

        engine = AnalogicalEngine()
        # 编码源领域结构（5 元组：源、目标、关系、源类型、目标类型）
        engine.encode_domain("调试", [
            ("症状", "原因", "causes", "problem", "cause"),
            ("原因", "工具", "requires", "cause", "tool"),
            ("工具", "症状", "mitigates", "tool", "problem"),
        ])
        # 查询结构相似领域
        analogies = engine.query_analogies("调试", limit=3)
        # 跨领域迁移
        advice = engine.transfer("调试", "优化", "性能瓶颈")
    """

    def __init__(self) -> None:
        self._domains: dict[str, StructuralGraph] = {}
        self._transfers: list[dict] = []

    # ── 编码 ──────────────────────────────────────────────────────────────

    def encode_domain(
        self,
        domain: str,
        relations: list[tuple[str, str, str, str, str]],
    ) -> StructuralGraph:
        """将领域经验编码为结构图。

        Args:
            domain: 领域名称。
            relations: 关系元组列表，固定 5 元组格式：
                (source, target, rel_type, source_type, target_type)
                rel_type 为 "causes"/"mitigates"/"requires"/"blocks"/
                "enables"/"part_of"；type 为 "problem"/"cause"/"solution"/
                "goal"/"constraint"/"tool"。
        """
        graph = StructuralGraph(domain=domain)
        for source, target, rel_name, source_type, target_type in relations:
            src_type = NodeType(source_type)
            tgt_type = NodeType(target_type)
            rel_type = RelType(rel_name)
            graph.add_relation(source, target, rel_type=rel_type, source_type=src_type, target_type=tgt_type)
        self._domains[domain] = graph
        return graph

    def get_domain(self, domain: str) -> StructuralGraph | None:
        return self._domains.get(domain)

    # ── 模式匹配 ──────────────────────────────────────────────────────────

    def match_pattern(self, domain: str) -> tuple[str, float]:
        """判断领域结构最匹配的预定义抽象模式。

        Returns:
            (模式名, 匹配度)。
        """
        graph = self._domains.get(domain)
        if not graph:
            return "", 0.0
        skeleton = set(graph.abstract())
        best_name, best_score = "", 0.0
        for name, template in _PATTERN_TEMPLATES.items():
            template_set = set(template)
            if not template_set:
                continue
            overlap = len(skeleton & template_set) / len(template_set)
            if overlap > best_score:
                best_name, best_score = name, overlap
        return best_name, best_score

    # ── 结构对齐 ──────────────────────────────────────────────────────────

    def structural_similarity(self, domain_a: str, domain_b: str) -> float:
        """两领域结构相似度（基于关系骨架重叠率）。"""
        ga = self._domains.get(domain_a)
        gb = self._domains.get(domain_b)
        if not ga or not gb:
            return 0.0
        sa = set(ga.abstract())
        sb = set(gb.abstract())
        if not sa or not sb:
            return 0.0
        union = sa | sb
        return len(sa & sb) / len(union)

    def query_analogies(self, domain: str, limit: int = 3) -> list[dict]:
        """查询与指定领域结构相似的领域。"""
        results: list[dict] = []
        for other in self._domains:
            if other == domain:
                continue
            score = self.structural_similarity(domain, other)
            if score > 0.0:
                results.append({"domain": other, "similarity": round(score, 3)})
        results.sort(key=lambda d: d["similarity"], reverse=True)
        return results[:limit]

    # ── 跨领域投射迁移 ────────────────────────────────────────────────────

    def transfer(self, source: str, target: str, target_problem: str = "") -> dict:
        """从源领域向目标领域迁移策略。

        对齐两领域骨架，将源领域中的 SOLUTION / TOOL 节点投射到目标领域，
        生成迁移建议。

        Returns:
            {"advice": [...], "confidence": float, "source": str, "target": str}
        """
        src_graph = self._domains.get(source)
        tgt_graph = self._domains.get(target)
        if not src_graph or not tgt_graph:
            return {"advice": [], "confidence": 0.0, "source": source, "target": target}

        # 对齐：目标骨架中的元素在源骨架中找到对应
        src_skeleton = set(src_graph.abstract())
        tgt_skeleton = set(tgt_graph.abstract())
        overlap = len(src_skeleton & tgt_skeleton) / max(len(tgt_skeleton), 1)
        confidence = overlap

        # 投射：源领域中的 SOLUTION / TOOL 节点作为可迁移策略
        strategies = [
            node.label
            for node in src_graph.nodes.values()
            if node.node_type in (NodeType.SOLUTION, NodeType.TOOL)
        ]
        advice = []
        if strategies:
            head = f"针对「{target_problem or target}」，可借鉴「{source}」的经验："
            advice.append(head)
            advice.extend(f"- {s}" for s in strategies[:3])
        self._transfers.append(
            {"source": source, "target": target, "confidence": confidence}
        )
        return {"advice": advice, "confidence": round(confidence, 3), "source": source, "target": target}

    # ── 状态 ──────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "domains": list(self._domains.keys()),
            "domain_count": len(self._domains),
            "transfers": self._transfers[-5:],
        }

    def to_summary(self, limit: int = 4) -> str:
        lines: list[str] = []
        for domain, graph in itertools.islice(self._domains.items(), limit):
            pattern, score = self.match_pattern(domain)
            lines.append(f"- {domain}（结构 {graph.node_count()} 节点，模式「{pattern}」匹配 {score:.0%}）")
        if not lines:
            return "暂无类比记忆"
        return "\n".join(lines)


__all__ = [
    "NodeType",
    "RelType",
    "Node",
    "Relation",
    "StructuralGraph",
    "AnalogicalEngine",
]
