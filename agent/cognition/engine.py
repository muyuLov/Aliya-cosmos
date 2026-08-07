"""认知引擎（CognitionEngine）

参考 LAAP（Living Agent Application Protocol）认知架构的集成模式，
为 Agent 提供三段式认知钩子，编排各认知模块：

    before_turn(user_text)  → 认知准备（需求 tick、交互记录、工作记忆）
    after_tool(name, result) → 工具学习（需求更新、情景记忆、世界模型、自我模型）
    after_turn(text, reply)  → 后续处理（对话记忆、记忆巩固、自主维护）

设计目标：
- 纯内存、无 IO 阻塞：所有钩子为轻量同步方法（工具结果直接传入）。
- 优雅降级：各模块独立，任一模块失败不影响整体。
- 认知上下文注入：build_context_injection() 聚合需求 / 世界 / 自我摘要，
  供 Agent 在 Prompt 中注入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.logger import get_logger

from agent.cognition.needs import NeedDriveSystem
from agent.cognition.memory import HierarchicalMemory
from agent.cognition.world_model import WorldModel, EntityType, RelationType
from agent.cognition.self_model import EmergentSelfModel
from agent.cognition.autonomy import AutonomyEngine, ActionProposal
from agent.cognition.learning import LearningPipeline
from agent.cognition.security import SecuritySystem, Verdict
from agent.cognition.conscious import ConsciousStream, Modality
from agent.cognition.meta_cognition import MetaCognitionMonitor
from agent.cognition.planner import Planner
from agent.cognition.causal import CausalEngine
from agent.cognition.analogical import AnalogicalEngine
from agent.cognition.evolution_system import EvolutionSystem
from agent.cognition import rust_bridge

logger = get_logger(__name__)

# 自主维护触发间隔（交互次数）
_AUTONOMY_MAINTENANCE_INTERVAL = 10


@dataclass
class CognitionConfig:
    """认知引擎配置"""

    needs_enabled: bool = True
    memory_enabled: bool = True
    world_model_enabled: bool = True
    self_model_enabled: bool = True
    autonomy_enabled: bool = True
    learning_enabled: bool = True
    security_enabled: bool = True
    conscious_enabled: bool = True
    meta_cognition_enabled: bool = True
    planner_enabled: bool = True
    causal_enabled: bool = True
    analogical_enabled: bool = True
    evolution_enabled: bool = True
    # 安全免疫：扫描用户输入（威胁命中时阻断）
    security_scan_user_input: bool = True
    # 自主维护：定期生成维护目标（记忆巩固、自我反思）
    autonomy_maintenance: bool = True
    maintenance_interval: int = _AUTONOMY_MAINTENANCE_INTERVAL


class CognitionEngine:
    """认知引擎：编排需求 / 记忆 / 世界模型 / 自我模型。"""

    def __init__(self, config: CognitionConfig | None = None) -> None:
        self.config = config or CognitionConfig()
        self.needs: NeedDriveSystem | None = (
            NeedDriveSystem() if self.config.needs_enabled else None
        )
        self.memory: HierarchicalMemory | None = (
            HierarchicalMemory() if self.config.memory_enabled else None
        )
        self.world: WorldModel | None = (
            WorldModel() if self.config.world_model_enabled else None
        )
        self.self_model: EmergentSelfModel | None = (
            EmergentSelfModel() if self.config.self_model_enabled else None
        )
        # 第二层：自主性 / 持续学习 / 安全免疫
        self.autonomy: AutonomyEngine | None = (
            AutonomyEngine() if self.config.autonomy_enabled else None
        )
        self.learning: LearningPipeline | None = (
            LearningPipeline() if self.config.learning_enabled else None
        )
        self.security: SecuritySystem | None = (
            SecuritySystem() if self.config.security_enabled else None
        )
        # 第三层：意识流 / 元认知 / 规划器
        self.conscious: ConsciousStream | None = (
            ConsciousStream() if self.config.conscious_enabled else None
        )
        self.meta_cognition: MetaCognitionMonitor | None = (
            MetaCognitionMonitor() if self.config.meta_cognition_enabled else None
        )
        self.planner: Planner | None = (
            Planner() if self.config.planner_enabled else None
        )
        # 第四层：因果推理 / 类比迁移
        self.causal: CausalEngine | None = (
            CausalEngine() if self.config.causal_enabled else None
        )
        self.analogical: AnalogicalEngine | None = (
            AnalogicalEngine() if self.config.analogical_enabled else None
        )
        # 第五层：进化系统（RSI 认知参数进化）
        self.evolution: EvolutionSystem | None = (
            EvolutionSystem() if self.config.evolution_enabled else None
        )
        # 加速实现模式（rust_bridge：纯 Python）
        self.accel_mode: str = rust_bridge.accel_mode()
        self._turn: int = 0
        self._autonomy_actions: list[str] = []
        self._security_alerts: list[str] = []
        self._pending_autonomy_proposals: list[ActionProposal] = []
        self._conscious_reflections: list[str] = []
        self._evolution_stats: dict = {"cycles": 0, "adopted": 0}

    # ── 三段式钩子 ────────────────────────────────────────────────────────

    def before_turn(self, user_text: str) -> None:
        """Step 1 认知准备：需求推进 + 交互记录 + 工作记忆 + 安全扫描。"""
        self._turn += 1
        if self.needs:
            self.needs.tick(dt=1.0)
            self.needs.record_user_interaction(positive=True)
            self.needs.record_energy_consumption(max(1, len(user_text) // 4))
        if self.memory:
            self.memory.attend(user_text, weight=1.0)
            self.memory.remember_episode(
                f"用户说：{user_text[:80]}", importance=0.4, context="user_input"
            )
        if self.world:
            self.world.add_entity(
                "用户", EntityType.USER, salience=0.8
            )
        # 安全扫描：威胁命中时记录，供 Agent 决策是否放行
        if self.security and self.config.security_scan_user_input:
            result = self.security.scan_user_input(user_text)
            if not result.passed:
                self._security_alerts.append(
                    f"[{result.layer}] {result.verdict.value}: "
                    + ", ".join(r.name for r in result.matched_rules)
                )
        # 持续学习：记录用户交互经验
        if self.learning:
            self.learning.record(
                content=user_text[:200],
                domain="user_interaction",
                success=True,
                importance=0.4,
            )
        # 意识流：登记用户感知（情感效价来自需求情绪梯度）
        if self.conscious:
            valence = 0.0
            if self.needs:
                gradient = self.needs.compute_emotion_gradient()
                valence = gradient["valence"]
            self.conscious.experience(
                content=f"用户说：{user_text[:60]}",
                modality=Modality.PERCEPTION,
                intensity=0.6,
                valence=valence,
            )
        # 元认知：追踪一次"用户交互决策"（为偏差检测积累样本）
        if self.meta_cognition:
            self.meta_cognition.track_decision(
                domain="interaction",
                strategy="respond",
                success=True,
            )
        # 自主行动生成：低频刷新候选（每 maintenance_interval 轮一次），
        # 避免每轮生成造成提案过期与 token 浪费
        if self._turn % max(self.config.maintenance_interval, 3) == 0:
            self._pending_autonomy_proposals = self._generate_autonomy_proposals()

    def after_tool(self, tool_name: str, success: bool, detail: Any = None) -> None:
        """Step 2 工具学习：需求更新 + 情景记忆 + 世界模型 + 自我模型。"""
        if self.needs:
            self.needs.record_tool_result(success=success)
        if self.memory:
            status = "成功" if success else "失败"
            detail_snippet = f"（{detail}）" if detail else ""
            self.memory.remember_episode(
                f"调用工具 {tool_name} {status}{detail_snippet}",
                importance=0.7 if not success else 0.4,
                context="tool_call",
            )
        if self.world:
            tool_eid = self.world.add_entity(
                tool_name, EntityType.TOOL,
                properties={"reliability": 0.9 if success else 0.3},
                confidence=0.8 if success else 0.5,
                salience=0.6,
            )
            user_eid = self._user_entity_id()
            if user_eid and tool_eid:
                self.world.add_relation(
                    user_eid, tool_eid,
                    RelationType.USES if success else RelationType.RELATES_TO,
                    confidence=0.8 if success else 0.4,
                )
            # 记录因果链（供因果推理引擎从世界模型构建）
            self.world.add_causal_link(
                f"调用{tool_name}",
                "问题解决" if success else "问题未解决",
                probability=0.85 if success else 0.3,
            )
        if self.self_model:
            self.self_model.record_experience(
                f"tools/{tool_name}",
                success=success,
                quality=0.9 if success else 0.2,
                predicted_confidence=0.7,
                was_surprising=not success,
            )
        # 安全扫描：工具结果（数据层敏感信息检测）
        if self.security and isinstance(detail, str):
            scan = self.security.scan_tool_result(tool_name, detail)
            if scan.verdict != Verdict.PASS:
                self._security_alerts.append(
                    f"[tool:{tool_name}] {scan.verdict.value}: "
                    + ", ".join(r.name for r in scan.matched_rules)
                )
        # 持续学习：记录工具调用经验（写入策略库）
        if self.learning:
            self.learning.record(
                content=f"{tool_name} {'成功' if success else '失败'}: {detail}",
                domain=f"tools/{tool_name}",
                success=success,
                importance=0.7 if not success else 0.4,
                surprise=0.6 if not success else 0.0,
            )
        # 意识流：登记工具执行感知
        if self.conscious:
            self.conscious.experience(
                content=f"我执行了 {tool_name} 并{'成功' if success else '失败'}",
                modality=Modality.COGNITION,
                intensity=0.5 if success else 0.8,
                valence=0.3 if success else -0.5,
            )
        # 元认知：追踪工具决策（供偏差检测）
        if self.meta_cognition:
            self.meta_cognition.track_decision(
                domain="tools",
                strategy=tool_name,
                success=success,
                confidence=0.7 if success else 0.3,
            )
        # 因果推理：观测工具可靠性变量
        if self.causal:
            self.causal.observe(
                f"tool:{tool_name}", 0.9 if success else 0.3, confidence=0.7
            )
            self.causal.graph.add_relation(
                f"tool:{tool_name}",
                "问题解决",
                strength=0.85 if success else 0.3,
            )
        # 类比迁移：将工具经验编码为领域结构（症状→工具→缓解）
        if self.analogical:
            graph = self.analogical.get_domain(f"tools/{tool_name}")
            if graph is None:
                self.analogical.encode_domain(
                    f"tools/{tool_name}",
                    [
                        ("问题", "原因", "causes", "problem", "cause"),
                        ("原因", f"{tool_name}", "requires", "cause", "tool"),
                        (f"{tool_name}", "问题", "mitigates", "tool", "problem"),
                    ],
                )

    def after_turn(self, reply: str) -> None:
        """Step 3 后续处理：对话记忆 + 记忆巩固 + 学习巩固 + 意识流反思 + 自主维护。"""
        if self.memory:
            self.memory.remember_episode(
                f"Aliya 回应：{reply[:80]}", importance=0.35, context="ai_reply"
            )
        # 意识流：登记回应体验，定期生成第一人称反思
        if self.conscious:
            self.conscious.experience(
                content=f"我对用户说：{reply[:60]}",
                modality=Modality.INTENTION,
                intensity=0.4,
            )
            if self._turn % 5 == 0:
                self._conscious_reflections = [self.conscious.reflect()]
        # 进化系统：记录性能指标（供 RSI 循环使用）
        if self.evolution:
            self.evolution.record_metric("task_success_rate", self._current_task_success_rate())
            self.evolution.record_metric("need_satisfaction", self._current_need_satisfaction())
            if self.needs:
                gradient = self.needs.compute_emotion_gradient()
                self.evolution.record_metric(
                    "emotion_instability", abs(gradient["valence"] - gradient["dominance"])
                )
        # 每轮轻度巩固；定期触发完整巩固
        if self._turn % 3 == 0:
            if self.memory:
                self.memory.consolidate()
            if self.learning:
                self.learning.consolidate(memory=self.memory)
        if self.config.autonomy_maintenance and self._turn % self.config.maintenance_interval == 0:
            self._run_autonomy_maintenance()
        # 进化循环：定期触发 RSI（观察→提案→评估→采纳）
        if self.evolution and self._turn % max(self.config.maintenance_interval * 3, 9) == 0:
            stats = self.evolution.run_evolution_cycle()
            self._evolution_stats["cycles"] += 1
            self._evolution_stats["adopted"] += stats["adopted"]

    def _run_autonomy_maintenance(self) -> None:
        """自主维护：巩固记忆、经验巩固、自我反思。"""
        if self.memory:
            self.memory.consolidate()
        if self.learning:
            self.learning.consolidate(memory=self.memory)
        if self.self_model:
            self.self_model.record_experience(
                "self/maintenance", success=True, quality=0.5
            )
        summary = self.build_context_injection(include_needs=False)
        self._autonomy_actions.append(summary or "维护完成")
        logger.debug("[Cognition] 自主维护完成 | turn=%d", self._turn)

    # ── 自主行动接口 ──────────────────────────────────────────────────────

    def _generate_autonomy_proposals(self) -> list[ActionProposal]:
        """基于需求赤字与目标停滞状态生成主动行动候选。"""
        if not self.autonomy:
            return []
        deficits = self.needs.compute_drive_vector() if self.needs else None
        return self.autonomy.generate_proposals(need_deficits=deficits)

    def get_autonomy_proposals(self) -> list[dict]:
        """返回当前可执行的主动行动建议（供 Agent 决策）。"""
        return [p.to_dict() for p in self._pending_autonomy_proposals]

    def mark_autonomy_executed(self, action: str) -> None:
        """登记一个主动行动已执行（满足自主性需求）。"""
        if not self.autonomy:
            return
        # 找到匹配的提案并标记执行
        for proposal in self._pending_autonomy_proposals:
            if proposal.action == action:
                self.autonomy.mark_executed(proposal)
                break
        if self.needs:
            self.needs.record_autonomy_action()

    # ── 认知上下文注入 ────────────────────────────────────────────────────

    def build_context_injection(
        self,
        include_needs: bool = True,
        limit: int = 5,
        max_sections: int | None = None,
    ) -> str:
        """聚合认知摘要，供 Agent 注入 system prompt / 对话上下文。

        Args:
            include_needs: 是否包含需求段。
            limit: 每个模块的条目上限。
            max_sections: 注入段数量上限（None = 全量）。Agent 可传
                有限值以控制 token 消耗。

        Returns:
            多段文本（每段由标题 + 内容组成，用空行分隔）。
        """
        parts: list[str] = []
        if include_needs and self.needs:
            needs_lines = []
            for need_type, need in self.needs.needs.items():
                needs_lines.append(
                    f"- {need_type.value}: 满足度 {need.satisfaction:.0%}（赤字 {need.deficit:.2f}）"
                )
            if needs_lines:
                parts.append("[内在需求]\n" + "\n".join(needs_lines))
        if self.world:
            world_summary = self.world.to_summary(limit=limit)
            if world_summary:
                parts.append("[世界认知]\n" + world_summary)
        if self.self_model:
            self_summary = self.self_model.to_summary(limit=limit)
            if self_summary:
                parts.append("[自我认知]\n" + self_summary)
        if self.conscious:
            narrative = self.conscious.narrative(limit=3)
            if narrative:
                parts.append("[意识流]\n" + narrative)
            if self._conscious_reflections:
                parts.append("[内心反思]\n" + self._conscious_reflections[-1])
        if self.causal:
            causal_summary = self.causal.graph.to_summary(limit=limit)
            if causal_summary:
                parts.append("[因果认知]\n" + causal_summary)
        if self.analogical:
            analogy_summary = self.analogical.to_summary(limit=limit)
            if analogy_summary:
                parts.append("[类比记忆]\n" + analogy_summary)
        if max_sections is not None:
            parts = parts[:max_sections]
        return "\n\n".join(parts)

    def build_memory_context(self, query: str = "", limit: int = 5) -> str:
        """召回记忆上下文（语义 + 情景 + 工作记忆）。"""
        if not self.memory:
            return ""
        parts = self.memory.build_context(query=query, limit=limit)
        return "\n".join(parts) if parts else ""

    # ── 需求驱动情绪 ──────────────────────────────────────────────────────

    def compute_emotion_gradient(self) -> dict[str, float] | None:
        """需求驱动情绪梯度（供推入 EmotionStateController）。

        Returns:
            {"valence": v, "arousal": a, "dominance": d}，需求系统禁用时返回 None。
        """
        if not self.needs:
            return None
        return self.needs.compute_emotion_gradient()

    def compute_intrinsic_reward(self) -> float | None:
        if not self.needs:
            return None
        return self.needs.compute_intrinsic_reward()

    # ── 状态报告 ──────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "turn": self._turn,
            "needs": self.needs.get_status() if self.needs else None,
            "memory": self.memory.get_stats() if self.memory else None,
            "world": self.world.get_stats() if self.world else None,
            "self_model": self.self_model.get_stats() if self.self_model else None,
            "autonomy": self.autonomy.get_status() if self.autonomy else None,
            "learning": self.learning.get_status() if self.learning else None,
            "security": self.security.get_status() if self.security else None,
            "conscious": self.conscious.get_status() if self.conscious else None,
            "meta_cognition": self.meta_cognition.get_status() if self.meta_cognition else None,
            "planner": self.planner.get_status() if self.planner else None,
            "causal": self.causal.get_status() if self.causal else None,
            "analogical": self.analogical.get_status() if self.analogical else None,
            "evolution": self.evolution.get_status() if self.evolution else None,
            "accel_mode": self.accel_mode,
            "autonomy_actions": self._autonomy_actions[-5:],
            "security_alerts": self._security_alerts[-5:],
            "conscious_reflections": self._conscious_reflections[-3:],
        }

    # ── 内部辅助 ──────────────────────────────────────────────────────────

    def _user_entity_id(self) -> str | None:
        if not self.world:
            return None
        users = self.world.query_entities(entity_type=EntityType.USER)
        return users[0].id if users else None

    def _current_task_success_rate(self) -> float:
        """当前任务成功率（从自我模型技能档案估计）。"""
        if not self.self_model or not self.self_model.skills:
            return 0.5
        skills = list(self.self_model.skills.values())
        attempts = sum(s.attempts for s in skills)
        if attempts == 0:
            return 0.5
        successes = sum(s.successes for s in skills)
        return successes / attempts

    def _current_need_satisfaction(self) -> float:
        """当前需求平均满足度。"""
        if not self.needs:
            return 0.5
        sats = [need.satisfaction for need in self.needs.needs.values()]
        return sum(sats) / len(sats) if sats else 0.5


__all__ = ["CognitionConfig", "CognitionEngine"]
