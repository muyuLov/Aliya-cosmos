"""Agent 运行配置（AgentConfig 与 YAML 加载）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.emotion import EmotionPersonality
from agent.emotion.vad import VADVector

# ── 默认配置常量 ───────────────────────────────────────────────────────────────
_PROGRESS_INTERVAL = 2.0
_MAX_TURNS = 10                # Agent 循环最大轮次（防止无限循环）
_MAX_REFINE_ACCUM = 10         # 最多保留多少条工具结果注入消息
_TOOL_PHASE_TIMEOUT = 60.0     # 工具阶段每轮 LLM 调用超时（秒）
_MAX_CONSECUTIVE_TIMEOUTS = 10  # 连续超时上限，超限后强制退出工具阶段


@dataclass
class AgentConfig:
    """Agent 运行配置"""
    max_turns: int = _MAX_TURNS
    progress_interval: float = _PROGRESS_INTERVAL
    max_refine_accum: int = _MAX_REFINE_ACCUM
    tool_format_version: str = "cot"  # "basic"=基础格式, "cot"=ReAct 格式
    cot_enabled: bool = True  # 思维链模式: true=启用, false=禁用
    reasoning_effort: str = "high"  # 思考强度: "high"/"max"/"low"
    # 权限配置文件路径（空字符串表示不启用配置驱动权限）
    permission_config_path: str = "data/config/Permissions.yml"
    # 以下为两阶段循环新增配置
    round_timeout: float = _TOOL_PHASE_TIMEOUT  # 工具阶段每轮超时
    max_consecutive_timeouts: int = _MAX_CONSECUTIVE_TIMEOUTS  # 连续超时上限
    compression_threshold: int = 80000  # 对话压缩阈值（字符数）
    max_soul_retries: int = 2  # 灵魂阶段净化失败时的重试次数（调用超时不重试）
    # 分层 Prompt 配置
    prompt_style: str = "default"  # 表达风格: default / lively / healing / sweet
    # 自动风格切换配置
    auto_style_enabled: bool = True  # 风格自动切换（纯 LLM 模式）
    # 情感系统配置（EmotionPersonality，None 使用默认人格）
    emotion_personality: EmotionPersonality | None = None
    # 情绪分类方式: rule（不创建向量分类器）/ vector / auto（向量可用则语义分类，否则 neutral 兜底）
    emotion_classifier: str = "auto"
    # 每类情绪最多向量化入库的样本数（0=全量；调大提升覆盖、调小缩短入库耗时）
    emotion_max_samples: int = 30
    # ── 认知引擎配置（LAAP 认知架构） ──
    cognition_enabled: bool = True  # 认知引擎总开关（需求驱动 / 五层记忆 / 世界模型 / 自我模型）
    cognition_maintenance_interval: int = 10  # 自主维护触发间隔（交互次数）


def _parse_bool(value: Any) -> bool:
    """宽松布尔解析。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1")


def _opt_float(raw: dict, key: str) -> float | None:
    """从字典读取可选的 float 字段。"""
    v = raw.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _parse_emotion_personality(raw: dict) -> EmotionPersonality | None:
    """从 YAML 字典构建 EmotionPersonality（缺失字段保持默认）。"""
    if not raw:
        return None
    baseline = raw.get("baseline")
    emotion_bias = raw.get("emotionBias") or raw.get("emotion_bias")
    return EmotionPersonality(
        baseline=VADVector.from_dict(baseline) if isinstance(baseline, dict) else None,
        reactivity=_opt_float(raw, "reactivity"),
        targetApproachRate=_opt_float(raw, "targetApproachRate") or _opt_float(raw, "target_approach_rate"),
        decayRate=_opt_float(raw, "decayRate") or _opt_float(raw, "decay_rate"),
        emotionHoldSeconds=_opt_float(raw, "emotionHoldSeconds") or _opt_float(raw, "emotion_hold_seconds"),
        emotionBias=dict(emotion_bias) if isinstance(emotion_bias, dict) else None,
        ambientDriftStrength=_opt_float(raw, "ambientDriftStrength") or _opt_float(raw, "ambient_drift_strength"),
    )


def agent_config_from_yaml(config_path: str = "data/config/main.yml") -> AgentConfig:
    """从 YAML 配置文件读取 Agent 相关配置。"""
    from core.config import get_config_instance
    cfg = get_config_instance(config_path)
    llm_section = cfg.get("cosmos.service.llm") or {}
    raw = llm_section.get("cot_enabled", True)
    cot_enabled = raw if isinstance(raw, bool) else (str(raw).strip().lower() in ("true", "yes", "1"))
    raw_effort = llm_section.get("reasoning_effort")
    reasoning_effort = raw_effort if isinstance(raw_effort, str) and raw_effort in ("high", "max", "low") else "high"
    # 权限配置路径
    agent_section = cfg.get("cosmos.service.agent") or {}
    perm_section = agent_section.get("permissions") or {}
    perm_config_path = str(perm_section.get("config_path", "data/config/Permissions.yml"))
    # 表达风格 + 自动切换配置
    prompt_section = cfg.get("cosmos.service.prompt") or {}
    style = str(prompt_section.get("style", "default"))
    auto_style = _parse_bool(prompt_section.get("auto_style", True))
    # 情感系统配置
    emotion_section = agent_section.get("emotion") if isinstance(agent_section.get("emotion"), dict) else {}
    if not isinstance(emotion_section, dict):
        emotion_section = {}
    emotion_personality = _parse_emotion_personality(emotion_section)
    emotion_classifier = str(emotion_section.get("classifier", "auto")).strip().lower()
    if emotion_classifier not in ("rule", "vector", "auto"):
        emotion_classifier = "auto"
    try:
        emotion_max_samples = int(emotion_section.get("max_samples_per_emotion", 30) or 30)
    except (TypeError, ValueError):
        emotion_max_samples = 30
    if emotion_max_samples < 0:
        emotion_max_samples = 30
    # 认知引擎配置
    cognition_section = agent_section.get("cognition") if isinstance(agent_section.get("cognition"), dict) else {}
    if not isinstance(cognition_section, dict):
        cognition_section = {}
    return AgentConfig(
        cot_enabled=cot_enabled, reasoning_effort=reasoning_effort,
        permission_config_path=perm_config_path,
        prompt_style=style,
        auto_style_enabled=auto_style,
        emotion_personality=emotion_personality,
        emotion_classifier=emotion_classifier,
        emotion_max_samples=emotion_max_samples,
        cognition_enabled=_parse_bool(cognition_section.get("enabled", True)),
        cognition_maintenance_interval=int(cognition_section.get("maintenance_interval", 10) or 10),
        max_soul_retries=int(agent_section.get("max_soul_retries", 2) or 2),
    )


__all__ = ["AgentConfig", "agent_config_from_yaml"]
