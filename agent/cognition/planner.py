"""规划器（Planner）

参考 LAAP（Living Agent Application Protocol）认知架构第 8.1 节，
实现层次化任务网络（HTN）目标分解的简化版。

核心思想：把高层的抽象目标分解为可执行的步骤序列，在执行中跟踪
进度，遇到停滞时重新规划。

组件：
1. PlanStep：单个计划步骤（动作 / 预期结果 / 状态）。
2. Plan：一个目标的完整计划（步骤序列 + 进度追踪）。
3. Planner：目标分解器——按目标类型选择分解模板，管理计划的
   创建、推进、停滞检测与重规划。

分解模板（面向陪伴场景）：
- learn（学习 / 探索）：了解 → 尝试 → 复盘
- comfort（安抚用户）：倾听 → 共情 → 建议
- engage（发起互动）：准备话题 → 邀请 → 反馈
- maintain（自主维护）：巩固 → 反思 → 更新
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger(__name__)


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class PlanStep:
    """一个计划步骤"""

    action: str
    expected_outcome: str = ""
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0

    def mark_done(self) -> None:
        self.status = StepStatus.DONE

    def mark_failed(self) -> None:
        self.status = StepStatus.FAILED

    def mark_running(self) -> None:
        self.status = StepStatus.RUNNING

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "expected_outcome": self.expected_outcome,
            "status": self.status.value,
            "attempts": self.attempts,
        }


@dataclass
class Plan:
    """一个目标的完整计划"""

    goal_title: str
    plan_type: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    max_attempts_per_step: int = 3

    def current_step(self) -> PlanStep | None:
        """当前待执行步骤（第一个非 DONE 的步骤）。"""
        for step in self.steps:
            if step.status != StepStatus.DONE:
                return step
        return None

    def is_complete(self) -> bool:
        return all(s.status == StepStatus.DONE for s in self.steps) and len(self.steps) > 0

    def progress(self) -> float:
        if not self.steps:
            return 0.0
        done = sum(1 for s in self.steps if s.status == StepStatus.DONE)
        return done / len(self.steps)

    def to_dict(self) -> dict:
        return {
            "goal_title": self.goal_title,
            "plan_type": self.plan_type,
            "progress": round(self.progress(), 3),
            "is_complete": self.is_complete(),
            "steps": [s.to_dict() for s in self.steps],
        }


# 分解模板：目标类型 → 步骤模板列表
_DECOMPOSITION_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "learn": [
        ("收集相关信息", "形成对目标领域的基本了解"),
        ("尝试一个具体行动", "获得一手经验"),
        ("复盘结果并提炼要点", "形成可复用认知"),
    ],
    "comfort": [
        ("认真倾听用户表达", "理解用户的情绪与需求"),
        ("表达共情与理解", "让用户感到被接纳"),
        ("提供温和的建议或陪伴", "缓解用户的情绪"),
    ],
    "engage": [
        ("准备一个有趣的话题", "引起用户兴趣"),
        ("发起对话邀请", "开启互动"),
        ("根据反馈调整话题", "维持互动的自然感"),
    ],
    "maintain": [
        ("巩固近期记忆", "提升记忆稳固性"),
        ("反思近期表现", "识别改进点"),
        ("更新自我认知", "保持自我模型的准确性"),
    ],
}


class Planner:
    """规划器。

    Usage::

        planner = Planner()
        plan_id = planner.create_plan("学会安慰用户", "comfort")
        step = planner.current_step(plan_id)
        planner.advance(plan_id, success=True)   # 当前步骤完成
        planner.replan(plan_id, reason="停滞")    # 重规划
    """

    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}
        self._id_counter: int = 0
        self._replan_count: int = 0

    def create_plan(self, goal_title: str, plan_type: str = "learn") -> str:
        """创建目标计划（按类型分解）。"""
        plan_type = plan_type if plan_type in _DECOMPOSITION_TEMPLATES else "learn"
        self._id_counter += 1
        plan_id = f"p{self._id_counter}"
        steps = [
            PlanStep(action=action, expected_outcome=outcome)
            for action, outcome in _DECOMPOSITION_TEMPLATES[plan_type]
        ]
        self._plans[plan_id] = Plan(
            goal_title=goal_title, plan_type=plan_type, steps=steps
        )
        return plan_id

    def get_plan(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def current_step(self, plan_id: str) -> PlanStep | None:
        plan = self._plans.get(plan_id)
        return plan.current_step() if plan else None

    def advance(self, plan_id: str, success: bool = True) -> None:
        """推进计划：当前步骤标记结果，前进到下一步。"""
        plan = self._plans.get(plan_id)
        if not plan:
            return
        step = plan.current_step()
        if not step:
            return
        step.attempts += 1
        if success:
            step.mark_done()
        else:
            # 超过单步最大尝试次数则标记失败（需重规划）
            if step.attempts >= plan.max_attempts_per_step:
                step.mark_failed()

    def is_complete(self, plan_id: str) -> bool:
        plan = self._plans.get(plan_id)
        return plan.is_complete() if plan else False

    def is_stalled(self, plan_id: str) -> bool:
        """停滞检测：当前步骤失败且超重试上限。"""
        plan = self._plans.get(plan_id)
        if not plan:
            return False
        step = plan.current_step()
        return step is not None and step.status == StepStatus.FAILED

    def replan(self, plan_id: str, reason: str = "") -> None:
        """重规划：重置失败步骤，可能换模板。"""
        plan = self._plans.get(plan_id)
        if not plan:
            return
        # 简单策略：重置所有失败步骤为 PENDING，尝试上限提高
        for step in plan.steps:
            if step.status in (StepStatus.FAILED, StepStatus.PENDING):
                step.status = StepStatus.PENDING
                step.attempts = 0
        plan.max_attempts_per_step += 2
        self._replan_count += 1
        if reason:
            logger.debug("[Planner] 重规划 %s: %s", plan_id, reason)

    def plans(self) -> list[Plan]:
        return list(self._plans.values())

    def get_status(self) -> dict:
        return {
            "plans": [p.to_dict() for p in self._plans.values()],
            "replan_count": self._replan_count,
            "active": sum(1 for p in self._plans.values() if not p.is_complete()),
        }


__all__ = ["StepStatus", "PlanStep", "Plan", "Planner"]
