"""持续学习管道（LearningPipeline）

参考 LAAP（Living Agent Application Protocol）认知架构第 7 章。

核心思想：Agent 应持续从经验中学习，而非一次性训练。实现：

1. 经验回放（ExperienceReplay）：优先级经验缓冲池。每条经验带
   优先级（importance × surprise），采样时按优先级加权，高频失败
   经验更容易被回放复习。
2. 策略库（PolicyLibrary）：从成功经验中提取"怎么做"的策略模板，
   关联领域（domain），带成功率统计。
3. 巩固引擎（ConsolidationEngine）：定期将高优先级经验巩固进
   长期记忆（调用外部 memory 层 / 更新策略库），模拟"睡眠巩固"。

与五层记忆的衔接：学习管道从工具调用 / 对话结果中收集经验，
巩固时将经验摘要写入 HierarchicalMemory 的情景 / 语义 / 程序层。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, cast

from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Experience:
    """一条经验"""

    content: str
    domain: str = "general"
    success: bool = True
    importance: float = 0.5
    surprise: float = 0.0  # 意外程度（低成功率 → 高意外）
    timestamp: float = field(default_factory=time.time)

    @property
    def priority(self) -> float:
        """回放优先级：importance 为主，意外程度加成。"""
        return self.importance + 0.3 * self.surprise

    def to_dict(self) -> dict:
        return {
            "content": self.content[:80],
            "domain": self.domain,
            "success": self.success,
            "importance": round(self.importance, 3),
            "surprise": round(self.surprise, 3),
            "priority": round(self.priority, 3),
        }


@dataclass
class PolicyTemplate:
    """策略模板（从经验提取的"怎么做"）"""

    name: str
    domain: str
    steps: list[str]
    success_rate: float = 0.5
    used_count: int = 0
    last_used_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "domain": self.domain,
            "steps": self.steps,
            "success_rate": round(self.success_rate, 3),
            "used_count": self.used_count,
        }


class ExperienceReplay:
    """优先级经验回放缓冲池。"""

    def __init__(self, capacity: int = 200) -> None:
        self._experiences: list[Experience] = []
        self._capacity = capacity

    def add(self, exp: Experience) -> None:
        self._experiences.append(exp)
        # 超容量时淘汰优先级最低者
        if len(self._experiences) > self._capacity:
            self._experiences.sort(key=lambda e: e.priority)
            self._experiences.pop(0)

    def sample(self, k: int = 3, rng: Callable[[], float] | None = None) -> list[Experience]:
        """按优先级加权采样 k 条经验。

        Args:
            k: 采样条数。
            rng: 确定性随机源（测试注入）。
        """
        if not self._experiences:
            return []
        weights = [max(e.priority, 0.01) for e in self._experiences]
        rng = rng or random.random
        result: list[Experience] = []
        pool = list(self._experiences)
        pool_weights = list(weights)
        for _ in range(min(k, len(pool))):
            target = rng() * sum(pool_weights)
            cumulative = 0.0
            for idx, exp in enumerate(pool):
                cumulative += pool_weights[idx]
                if cumulative >= target:
                    result.append(exp)
                    del pool[idx]
                    del pool_weights[idx]
                    break
        return result

    def recent_failures(self, limit: int = 5) -> list[Experience]:
        """最近失败经验（用于复盘）。"""
        fails = [e for e in self._experiences if not e.success]
        fails.sort(key=lambda e: e.timestamp, reverse=True)
        return fails[:limit]

    def __len__(self) -> int:
        return len(self._experiences)


class PolicyLibrary:
    """策略库：从成功经验提取可复用操作序列。"""

    def __init__(self) -> None:
        self._policies: dict[str, PolicyTemplate] = {}

    def add_policy(self, name: str, domain: str, steps: list[str], success_rate: float = 0.5) -> None:
        self._policies[name] = PolicyTemplate(
            name=name, domain=domain, steps=list(steps), success_rate=success_rate
        )

    def get(self, name: str) -> PolicyTemplate | None:
        policy = self._policies.get(name)
        if policy:
            policy.used_count += 1
            policy.last_used_at = time.time()
        return policy

    def get_for_domain(self, domain: str) -> list[PolicyTemplate]:
        return [p for p in self._policies.values() if p.domain == domain]

    def record_outcome(self, name: str, success: bool) -> None:
        """记录一次策略应用结果，更新成功率。"""
        policy = self._policies.get(name)
        if not policy:
            return
        old = policy.success_rate
        # 滑动平均更新
        policy.success_rate = old + (0.2 * ((1.0 if success else 0.0) - old))

    def __len__(self) -> int:
        return len(self._policies)


class ConsolidationEngine:
    """巩固引擎：定期将高价值经验固化为策略 / 长期记忆。"""

    def __init__(self, replay: ExperienceReplay, policy_library: PolicyLibrary) -> None:
        self._replay = replay
        self._policies = policy_library

    def consolidate(
        self,
        memory: object | None = None,
        min_priority: float = 0.8,
    ) -> dict:
        """执行一轮巩固。

        Args:
            memory: 可选外部记忆层（HierarchicalMemory），用于写入
                语义 / 程序记忆。
            min_priority: 参与巩固的最小优先级。

        Returns:
            巩固统计 {"replayed": n, "policies_added": m, "memories": k}
        """
        stats = {"replayed": 0, "policies_added": 0, "memories": 0}

        # 1) 回放高优先级经验
        for exp in self._replay.sample(k=5):
            if exp.priority < min_priority:
                continue
            stats["replayed"] += 1

            # 2) 成功经验 → 提取策略模板
            if exp.success:
                policy_name = f"{exp.domain}:{exp.content[:20]}"
                if policy_name not in self._policies._policies:
                    self._policies.add_policy(
                        name=policy_name,
                        domain=exp.domain,
                        steps=[exp.content],
                        success_rate=1.0,
                    )
                    stats["policies_added"] += 1

            # 3) 写入长期记忆层
            memory_any = cast(Any, memory)
            if memory is not None and hasattr(memory, "learn_fact"):
                memory_any.learn_fact(
                    f"experience:{exp.domain}",
                    exp.content,
                    confidence=0.8 if exp.success else 0.4,
                    source="consolidation",
                )
                stats["memories"] += 1

        return stats


class LearningPipeline:
    """持续学习管道（聚合三组件）。

    Usage::

        lp = LearningPipeline()
        lp.record(content="用户喜欢咖啡", domain="preference", success=True, importance=0.8)
        replayed = lp.replay_sample(k=3)
        stats = lp.consolidate(memory=hierarchical_memory)
    """

    def __init__(self, capacity: int = 200) -> None:
        self.replay = ExperienceReplay(capacity=capacity)
        self.policies = PolicyLibrary()
        self.consolidator = ConsolidationEngine(self.replay, self.policies)
        self._total_records: int = 0

    def record(
        self,
        content: str,
        domain: str = "general",
        success: bool = True,
        importance: float = 0.5,
        surprise: float = 0.0,
    ) -> None:
        """记录一条经验。"""
        self.replay.add(
            Experience(
                content=content,
                domain=domain,
                success=success,
                importance=importance,
                surprise=surprise,
            )
        )
        self._total_records += 1

    def replay_sample(self, k: int = 3) -> list[Experience]:
        return self.replay.sample(k=k)

    def recent_failures(self, limit: int = 5) -> list[Experience]:
        return self.replay.recent_failures(limit=limit)

    def consolidate(self, memory: object | None = None) -> dict:
        return self.consolidator.consolidate(memory=memory)

    def get_status(self) -> dict:
        return {
            "experiences": len(self.replay),
            "total_records": self._total_records,
            "policies": len(self.policies),
            "recent_failures": [e.to_dict() for e in self.recent_failures(limit=3)],
        }


__all__ = [
    "Experience",
    "PolicyTemplate",
    "ExperienceReplay",
    "PolicyLibrary",
    "ConsolidationEngine",
    "LearningPipeline",
]
