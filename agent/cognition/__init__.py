"""认知层（Cognition Layer）

参考 LAAP（Living Agent Application Protocol）认知架构，为 Aliya 提供
类脑认知功能：

- needs：PSI 需求驱动系统（五大需求 + 需求驱动情绪梯度）
- memory：五层层次化记忆（工作/情景/语义/程序/向量）
- world_model：世界模型（实体-关系图 + 贝叶斯信念更新 + 前向预测）
- self_model：涌现自我模型（技能档案 + 置信度校准 + 自传体叙事）
- autonomy：自主性引擎（目标管理 + 停滞检测 + 主动行动）
- learning：持续学习管道（经验回放 + 策略库 + 巩固）
- security：安全免疫系统（威胁扫描 + 审计）
- conscious：意识流（Qualia + 注意力 + 叙事 + 反思）
- meta_cognition：元认知监控（认知偏差检测 + 思考模式推荐）
- planner：规划器（目标分解 + 停滞重规划）
- causal：因果推理引擎（关联 / 干预 / 反事实三层级）
- analogical：类比迁移引擎（结构映射 + 跨领域迁移）
- evolution：进化系统（RSI 认知参数进化）
- rust_bridge：双轨加速桥接层（原生扩展 + python/numpy 降级）
- engine：认知引擎（三段式钩子 before_turn / after_tool / after_turn）
"""

from __future__ import annotations

from agent.cognition.needs import Need, NeedDriveSystem, NeedType
from core.memory.hierarchical import HierarchicalMemory
from agent.cognition.world_model import WorldModel
from agent.cognition.self_model import EmergentSelfModel
from agent.cognition.autonomy import AutonomyEngine, Goal, GoalStatus, Priority
from agent.cognition.learning import LearningPipeline, Experience
from agent.cognition.security import SecuritySystem, Verdict, ScanResult
from agent.cognition.conscious import ConsciousStream, Modality, Quale
from agent.cognition.meta_cognition import MetaCognitionMonitor, BiasReport
from agent.cognition.planner import Planner, Plan, PlanStep, StepStatus
from agent.cognition.causal import CausalEngine, CausalGraph, CausalVariable, CausalRelation
from agent.cognition.analogical import (
    AnalogicalEngine,
    StructuralGraph,
    NodeType,
    RelType,
    Node,
    Relation,
)
from agent.cognition.evolution_system import EvolutionSystem, EvolutionProposal
from agent.cognition.engine import CognitionConfig, CognitionEngine

__all__ = [
    "Need",
    "NeedType",
    "NeedDriveSystem",
    "HierarchicalMemory",
    "WorldModel",
    "EmergentSelfModel",
    "AutonomyEngine",
    "Goal",
    "GoalStatus",
    "Priority",
    "LearningPipeline",
    "Experience",
    "SecuritySystem",
    "Verdict",
    "ScanResult",
    "ConsciousStream",
    "Modality",
    "Quale",
    "MetaCognitionMonitor",
    "BiasReport",
    "Planner",
    "Plan",
    "PlanStep",
    "StepStatus",
    "CausalEngine",
    "CausalGraph",
    "CausalVariable",
    "CausalRelation",
    "AnalogicalEngine",
    "StructuralGraph",
    "NodeType",
    "RelType",
    "Node",
    "Relation",
    "EvolutionSystem",
    "EvolutionProposal",
    "CognitionConfig",
    "CognitionEngine",
]
