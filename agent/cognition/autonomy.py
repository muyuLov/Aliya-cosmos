"""自主性引擎（AutonomyEngine）

参考 LAAP（Living Agent Application Protocol）认知架构第 8 章。

核心思想：Agent 不应仅被动响应外部指令，还应拥有内在驱动的自主行为
（"自主维护循环"）。自主性引擎维护目标、追踪进度、检测停滞，并在
合适的时机提出主动行动建议，交由 Agent 决策是否执行。

三大机制：
1. 目标管理（GoalManager）：目标分层（主目标 → 子目标），带优先级 /
   截止时间 / 完成状态 / 关联需求。
2. 停滞检测（StagnationDetector）：跟踪目标进度，若长时间无进展，
   标记"停滞"，触发重新规划 / 主动求助。
3. 主动行动生成（ActionGenerator）：基于停滞目标与需求赤字，生成
   候选主动行动（"我可以…"），供 Agent 在适当时机提出。

与需求系统的闭环：当 AUTONOMY（自主性）需求赤字升高时，引擎倾向
生成更多主动行动；执行主动行动会满足该需求（needs.record_autonomy_action）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from core.logger import get_logger

logger = get_logger(__name__)


class GoalStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    STAGNANT = "stagnant"


class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class Goal:
    """一个目标"""

    title: str
    priority: Priority = Priority.MEDIUM
    description: str = ""
    parent_id: str | None = None  # 子目标归属的主目标
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    progress: float = 0.0  # 0-1
    # 关联需求类型（如 NeedType.AUTONOMY.value），用于驱动生成
    related_need: str | None = None

    def report_progress(self, delta: float = 0.1) -> None:
        """汇报进度。"""
        self.progress = max(0.0, min(1.0, self.progress + delta))
        self.last_progress_at = time.time()
        if self.progress >= 1.0:
            self.status = GoalStatus.COMPLETED

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def stagnant_seconds(self) -> float:
        return time.time() - self.last_progress_at

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "priority": self.priority.value,
            "description": self.description,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "age_seconds": round(self.age_seconds, 1),
            "stagnant_seconds": round(self.stagnant_seconds, 1),
        }


@dataclass
class ActionProposal:
    """主动行动建议"""

    action: str
    reason: str
    related_goal_id: str | None = None
    related_need: str | None = None
    priority: Priority = Priority.MEDIUM

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "related_goal_id": self.related_goal_id,
            "related_need": self.related_need,
            "priority": self.priority.value,
        }


class AutonomyEngine:
    """自主性引擎。

    Usage::

        engine = AutonomyEngine()
        gid = engine.add_goal("熟悉用户的兴趣爱好", priority=Priority.HIGH)
        engine.report_progress(gid, 0.5)
        proposals = engine.generate_proposals()   # 建议主动行动
        engine.mark_executed(proposals[0])        # 执行后登记
    """

    def __init__(
        self,
        stagnation_threshold: float = 3600.0,  # 1 小时无进展视为停滞
        max_active_goals: int = 5,
    ) -> None:
        self._goals: dict[str, Goal] = {}
        self._id_counter: int = 0
        self._stagnation_threshold = stagnation_threshold
        self._max_active_goals = max_active_goals
        self._executed_actions: list[str] = []

    # ── 目标管理 ──────────────────────────────────────────────────────────

    def add_goal(
        self,
        title: str,
        priority: Priority = Priority.MEDIUM,
        description: str = "",
        parent_id: str | None = None,
        related_need: str | None = None,
    ) -> str:
        """添加目标。超过最大活跃目标数时返回空字符串（拒绝）。"""
        active = self._active_goals()
        if len(active) >= self._max_active_goals and parent_id is None:
            logger.debug("[Autonomy] 活跃目标已达上限（%d），拒绝新目标", self._max_active_goals)
            return ""
        self._id_counter += 1
        gid = f"g{self._id_counter}"
        self._goals[gid] = Goal(
            title=title,
            priority=priority,
            description=description,
            parent_id=parent_id,
            related_need=related_need,
        )
        return gid

    def report_progress(self, goal_id: str, delta: float = 0.1) -> None:
        """汇报目标进度，自动标记完成。"""
        goal = self._goals.get(goal_id)
        if goal and goal.status == GoalStatus.ACTIVE:
            goal.report_progress(delta)

    def abandon_goal(self, goal_id: str) -> None:
        goal = self._goals.get(goal_id)
        if goal and goal.status == GoalStatus.ACTIVE:
            goal.status = GoalStatus.ABANDONED

    def get_goal(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def active_goals(self) -> list[Goal]:
        """返回活跃或停滞中的目标（供外部查看）。"""
        return [g for g in self._goals.values() if g.status in (GoalStatus.ACTIVE, GoalStatus.STAGNANT)]

    def _active_goals(self) -> list[Goal]:
        return [g for g in self._goals.values() if g.status == GoalStatus.ACTIVE]

    # ── 停滞检测 ──────────────────────────────────────────────────────────

    def detect_stagnation(self) -> list[str]:
        """返回所有停滞目标的 ID 列表（超阈值无进展）。"""
        stagnant: list[str] = []
        for gid, goal in self._goals.items():
            if goal.status == GoalStatus.ACTIVE and goal.stagnant_seconds >= self._stagnation_threshold:
                goal.status = GoalStatus.STAGNANT
                stagnant.append(gid)
        return stagnant

    # ── 主动行动生成 ──────────────────────────────────────────────────────

    def generate_proposals(self, need_deficits: dict[str, float] | None = None) -> list[ActionProposal]:
        """基于停滞目标与需求赤字生成主动行动建议。

        Args:
            need_deficits: 需求赤字字典（如 {"autonomy": 0.4}），
                赤字高的需求更倾向产生主动行动。

        Returns:
            按优先级排序的行动建议列表。
        """
        proposals: list[ActionProposal] = []
        need_deficits = need_deficits or {}

        # 1) 停滞目标 → 建议重新规划 / 求助
        for gid, goal in self._goals.items():
            if goal.status == GoalStatus.STAGNANT:
                proposals.append(
                    ActionProposal(
                        action=f"重新规划目标「{goal.title}」或向用户求助",
                        reason=f"目标长期无进展（{int(goal.stagnant_seconds)}s 未更新）",
                        related_goal_id=gid,
                        related_need=goal.related_need,
                        priority=goal.priority,
                    )
                )

        # 2) 主动维护：长期未执行的主动行为 → 提出"我最近没主动做点什么"
        if need_deficits.get("autonomy", 0.0) >= 0.3:
            proposals.append(
                ActionProposal(
                    action="主动关心用户近况或提出一个有趣的话题",
                    reason="自主性需求赤字偏高，需要主动互动",
                    related_need="autonomy",
                    priority=Priority.MEDIUM,
                )
            )

        # 3) 关联需求赤字较高的活跃目标 → 主动推进
        for gid, goal in self._goals.items():
            if goal.status != GoalStatus.ACTIVE:
                continue
            if goal.related_need and need_deficits.get(goal.related_need, 0.0) >= 0.25:
                proposals.append(
                    ActionProposal(
                        action=f"推进目标「{goal.title}」（当前进度 {goal.progress:.0%}）",
                        reason=f"关联需求 {goal.related_need} 赤字高，应主动推进",
                        related_goal_id=gid,
                        related_need=goal.related_need,
                        priority=goal.priority,
                    )
                )

        # 按优先级排序（高优先在前）
        proposals.sort(key=lambda p: p.priority.value, reverse=True)
        return proposals

    def mark_executed(self, proposal: ActionProposal) -> None:
        """登记一个主动行动已执行。"""
        self._executed_actions.append(proposal.action)
        if proposal.related_goal_id:
            self.report_progress(proposal.related_goal_id, delta=0.2)

    # ── 序列化 ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "goals": [g.to_dict() for g in self._goals.values()],
            "active_count": len(self._active_goals()),
            "stagnant_count": len(self.detect_stagnation()),
            "executed_actions": self._executed_actions[-10:],
        }


__all__ = ["GoalStatus", "Priority", "Goal", "ActionProposal", "AutonomyEngine"]
