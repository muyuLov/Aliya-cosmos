"""因果推理引擎（CausalEngine）

参考 LAAP（Living Agent Application Protocol）认知架构第 6 章，
实现 Pearl 因果层级（causal ladder）的简化版本。

三个层级：
- Level 1 关联（Association）：P(Y | X)——在观测层面统计相关性。
- Level 2 干预（Intervention）：P(Y | do(X = x))——通过图切割
  （graph surgery）切断指向 X 的边后计算效应。
- Level 3 反事实（Counterfactual）："如果当时 X 不是 x，Y 会怎样？"
  ——采用三步法：溯因（abduction，推断扰动）→ 行动（action，
  施加干预）→ 预测（prediction，重算结果）。

数据结构：
- CausalVariable：变量节点（当前值 + 置信度）。
- CausalRelation：因果边（方向 + 强度 + 置信度 + 证据）。
- CausalGraph：有向无环图（DAG），支持邻接查询、贝叶斯更新、
  图切割干预、多步传播。

与世界模型的衔接：CausalEngine.build_from_world_model() 从
WorldModel 的因果链（CausalLink）构建推理图。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class CausalVariable:
    """因果变量节点"""

    name: str
    value: float = 0.5  # 当前水平 [0, 1]
    confidence: float = 0.5
    source: str = ""  # 来源（如 "user_input" / "tool_result"）
    observed_at: float = field(default_factory=time.time)

    def update(self, value: float, confidence: float) -> None:
        """贝叶斯加权更新。"""
        old_weight = self.confidence / max(self.confidence + confidence, 1e-9)
        self.value = _clamp(old_weight * self.value + (1 - old_weight) * value, 0.0, 1.0)
        self.confidence = _clamp(self.confidence + 0.2 * confidence, 0.0, 1.0)
        self.observed_at = time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "confidence": round(self.confidence, 3),
            "source": self.source,
        }


@dataclass
class CausalRelation:
    """因果边：source 导致 target"""

    source: str
    target: str
    strength: float = 0.5  # 效应强度 [-1, 1]，负值表示抑制
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "strength": round(self.strength, 3),
            "confidence": round(self.confidence, 3),
        }


class CausalGraph:
    """因果图（DAG）。

    Usage::

        g = CausalGraph()
        g.add_variable("努力", 0.6)
        g.add_variable("成绩", 0.4)
        g.add_relation("努力", "成绩", strength=0.8)
        g.observe("成绩", 0.9)
    """

    def __init__(self) -> None:
        self.variables: dict[str, CausalVariable] = {}
        self.relations: list[CausalRelation] = []
        # 邻接表（缓存）
        self._children: dict[str, list[str]] = {}
        self._parents: dict[str, list[str]] = {}

    # ── 构建 ──────────────────────────────────────────────────────────────

    def add_variable(self, name: str, value: float = 0.5, confidence: float = 0.5, source: str = "") -> None:
        name = name.strip()
        if not name:
            return
        if name in self.variables:
            return  # 已存在，不覆盖（用 observe 更新）
        self.variables[name] = CausalVariable(
            name=name, value=value, confidence=confidence, source=source
        )
        self._children.setdefault(name, [])
        self._parents.setdefault(name, [])

    def add_relation(self, source: str, target: str, strength: float = 0.5, confidence: float = 0.5) -> None:
        self.add_variable(source)
        self.add_variable(target)
        # 防重复边
        for rel in self.relations:
            if rel.source == source and rel.target == target:
                rel.strength = _clamp(rel.strength + strength, -1.0, 1.0)
                rel.confidence = _clamp(rel.confidence + 0.1 * confidence, 0.0, 1.0)
                return
        self.relations.append(
            CausalRelation(source=source, target=target, strength=strength, confidence=confidence)
        )
        self._children.setdefault(source, []).append(target)
        self._parents.setdefault(target, []).append(source)

    # ── 观测与更新 ────────────────────────────────────────────────────────

    def observe(self, variable: str, value: float, confidence: float = 0.8) -> None:
        """观测到变量的值 → 贝叶斯更新。"""
        if variable not in self.variables:
            self.add_variable(variable, value=value, confidence=confidence, source="observation")
        else:
            self.variables[variable].update(value, confidence)

    def get(self, name: str) -> CausalVariable | None:
        return self.variables.get(name)

    # ── Level 1：关联查询 ────────────────────────────────────────────────

    def query_association(self, target: str, condition: str | None = None) -> dict:
        """关联层查询：P(target | condition)。

        从 condition 出发沿因果边传播到 target（简化线性传播）。

        Returns:
            {"target": t, "condition": c, "value": v, "confidence": c}
        """
        if target not in self.variables:
            return {"target": target, "condition": condition, "value": 0.5, "confidence": 0.0}
        if condition is None:
            var = self.variables[target]
            return {"target": target, "condition": None, "value": var.value, "confidence": var.confidence}

        # 前向传播：从 condition 开始，沿边（strength 加权）累计到 target
        if self._has_path(condition, target):
            propagated = self._propagate(condition, target)
            return {
                "target": target,
                "condition": condition,
                "value": propagated,
                "confidence": self.variables[target].confidence * 0.8,
            }
        # 无路径：返回无条件观测
        var = self.variables[target]
        return {"target": target, "condition": condition, "value": var.value, "confidence": var.confidence}

    def _has_path(self, start: str, end: str, visited: set[str] | None = None) -> bool:
        if start == end:
            return True
        visited = visited or set()
        if start in visited:
            return False
        visited.add(start)
        for child in self._children.get(start, []):
            if self._has_path(child, end, visited):
                return True
        return False

    def _propagate(self, start: str, end: str) -> float:
        """沿路径线性传播值（简化：沿每条路径累计 strength 加权）。"""
        paths = self._enumerate_paths(start, end)
        if not paths:
            return self.variables[end].value
        source_val = self.variables[start].value
        total = 0.0
        for path in paths:
            product = source_val
            for i in range(len(path) - 1):
                rel = self._find_relation(path[i], path[i + 1])
                if rel:
                    # 简单传播：值沿边缩放（strength 折半衰减）
                    product = product * max(0.0, rel.strength) * 0.7 + rel.strength * 0.3
            total += product
        return _clamp(total / len(paths), 0.0, 1.0)

    def _enumerate_paths(self, start: str, end: str, max_depth: int = 4) -> list[list[str]]:
        paths: list[list[str]] = []

        def dfs(node: str, path: list[str], depth: int) -> None:
            if depth > max_depth:
                return
            if node == end:
                paths.append(list(path))
                return
            for child in self._children.get(node, []):
                if child not in path:
                    dfs(child, path + [child], depth + 1)

        dfs(start, [start], 0)
        return paths

    def _find_relation(self, source: str, target: str) -> CausalRelation | None:
        for rel in self.relations:
            if rel.source == source and rel.target == target:
                return rel
        return None

    # ── Level 2：干预（do-算子，图切割） ─────────────────────────────────

    def intervened_copy(self, variable: str, value: float) -> "CausalGraph":
        """图切割：复制图并切断指向 variable 的入边，强制设为 value。"""
        new_graph = CausalGraph()
        for name, var in self.variables.items():
            new_graph.add_variable(name, value=var.value, confidence=var.confidence, source=var.source)
        for rel in self.relations:
            if rel.target == variable:
                continue  # 切断指向干预变量的入边
            new_graph.add_relation(rel.source, rel.target, strength=rel.strength, confidence=rel.confidence)
        # 强制设置干预值
        new_graph.observe(variable, value, confidence=1.0)
        return new_graph

    def predict_effect(self, intervention_var: str, intervention_value: float, target: str) -> dict:
        """干预层查询：P(target | do(intervention_var = value))。"""
        if intervention_var not in self.variables:
            return {"intervention": intervention_var, "target": target, "value": 0.5, "confidence": 0.0}
        if target not in self.variables:
            return {"intervention": intervention_var, "target": target, "value": 0.5, "confidence": 0.0}
        g2 = self.intervened_copy(intervention_var, intervention_value)
        predicted = g2._propagate(intervention_var, target)
        return {
            "intervention": intervention_var,
            "intervention_value": intervention_value,
            "target": target,
            "value": round(predicted, 3),
            "confidence": round(self.variables[target].confidence * 0.7, 3),
        }

    # ── Level 3：反事实 ──────────────────────────────────────────────────

    def counterfactual(
        self,
        variable: str,
        actual_value: float,
        hypothetical_value: float,
        target: str,
    ) -> dict:
        """反事实查询：给定 variable 实际为 actual_value，假设改为
        hypothetical_value，则 target 会怎样？

        三步法（简化）：
        1. 溯因：记录实际观测（variable 的当前值与扰动）。
        2. 行动：图切割施加干预 variable = hypothetical_value。
        3. 预测：在干预图上传播到 target。
        """
        if variable not in self.variables or target not in self.variables:
            return {"error": "missing_variable"}
        # 实际路径：当前图传播
        actual_effect = self._propagate(variable, target)
        # 反事实：干预后传播
        g2 = self.intervened_copy(variable, hypothetical_value)
        counterfactual_effect = g2._propagate(variable, target)
        difference = counterfactual_effect - actual_effect
        return {
            "variable": variable,
            "actual_value": actual_value,
            "hypothetical_value": hypothetical_value,
            "target": target,
            "actual_effect": round(actual_effect, 3),
            "counterfactual_effect": round(counterfactual_effect, 3),
            "difference": round(difference, 3),
            "explanation": (
                f"若「{variable}」由 {actual_value:.1f} 改为 {hypothetical_value:.1f}，"
                f"「{target}」预计从 {actual_effect:.2f} 变为 {counterfactual_effect:.2f}"
            ),
        }

    # ── 序列化 ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "variables": len(self.variables),
            "relations": len(self.relations),
        }

    def to_summary(self, limit: int = 8) -> str:
        lines: list[str] = []
        for rel in self.relations[:limit]:
            src = self.variables.get(rel.source)
            tgt = self.variables.get(rel.target)
            if src and tgt:
                arrow = "→" if rel.strength >= 0 else "⊣"
                lines.append(
                    f"- {src.name}({src.value:.2f}) {arrow} {tgt.name} "
                    f"(强度 {rel.strength:.2f}, 置信 {rel.confidence:.2f})"
                )
        return "\n".join(lines)


class CausalEngine:
    """因果推理引擎（聚合图 + 从世界模型构建）。

    Usage::

        engine = CausalEngine()
        engine.graph.add_variable("用户满意", 0.5)
        engine.observe("用户满意", 0.8)
        effect = engine.predict_effect("关怀", 1.0, "用户满意")
    """

    def __init__(self) -> None:
        self.graph = CausalGraph()

    def observe(self, variable: str, value: float, confidence: float = 0.8) -> None:
        """观测变量值（供 Agent 在对话中调用）。"""
        self.graph.observe(variable, value, confidence)

    def build_from_world_model(self, world_model: Any) -> int:
        """从世界模型的因果链构建推理图。

        Args:
            world_model: WorldModel 实例。

        Returns:
            导入的因果边数量。
        """
        count = 0
        if world_model is None or not hasattr(world_model, "_causal_links"):
            return 0
        for link in world_model._causal_links:
            self.graph.add_relation(
                source=link.condition,
                target=link.effect,
                strength=link.probability,
                confidence=link.confidence,
            )
            count += 1
        return count

    def get_status(self) -> dict:
        return {"graph": self.graph.get_stats(), "summary": self.graph.to_summary()}


__all__ = [
    "CausalVariable",
    "CausalRelation",
    "CausalGraph",
    "CausalEngine",
]
