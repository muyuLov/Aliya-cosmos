"""GRAG 配置加载模块"""

import threading
from dataclasses import dataclass, field
from typing import Optional

from core.config import get_config_instance


@dataclass
class Neo4jConfig:
    """Neo4j 连接配置"""
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str | None = None
    database: str = "neo4j"


@dataclass
class ExtractorConfig:
    """五元组提取器配置"""
    max_retries: int = 2
    timeout: int = 30


@dataclass
class TaskManagerConfig:
    """任务管理器配置"""
    max_workers: int = 3
    max_queue_size: int = 100
    task_timeout: int = 30
    auto_cleanup_hours: int = 24


@dataclass
class GRAGConfig:
    """GRAG 知识图谱记忆系统配置"""
    enabled: bool = True
    auto_extract: bool = True
    context_length: int = 10
    similarity_threshold: float = 0.7
    session_tracking: bool = True
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    task_manager: TaskManagerConfig = field(default_factory=TaskManagerConfig)


# 全局配置实例 - 懒加载从统一配置管理器读取
_config: Optional[GRAGConfig] = None
_config_initialized = False
_config_lock = threading.Lock()


def _on_config_change(path: str, value: object) -> None:
    """配置变更回调，清除缓存"""
    global _config
    with _config_lock:
        if path.startswith("cosmos.service.grag"):
            _config = None


def init_config_listener() -> None:
    """初始化配置变更监听器（在应用启动时调用）"""
    global _config_initialized
    if _config_initialized:
        return
    with _config_lock:
        # Double-check 模式：获取锁后重新检查状态
        if _config_initialized:
            return
        cfg = get_config_instance("data/config/main.yml")
        cfg.register_callback("cosmos.service.grag", _on_config_change)
        _config_initialized = True


def get_grag_config(config_path: str = "data/config/main.yml") -> GRAGConfig:
    """
    获取 GRAG 配置。
    
    使用统一的配置管理器单例，确保配置状态一致。
    首次调用时自动注册配置变更监听器。
    配置变更时自动清除缓存，下次调用时重新加载。
    """
    global _config
    init_config_listener()
    if _config is None:
        with _config_lock:
            # Double-check 模式：获取锁后重新检查状态
            if _config is None:
                _config = _load_grag_config(config_path)
    return _config


def _load_grag_config(config_path: str) -> GRAGConfig:
    """从配置文件加载 GRAG 配置"""
    cfg = get_config_instance(config_path)

    # 加载顶层配置
    grag_enabled = cfg.get("cosmos.service.grag.enabled", True)
    auto_extract = cfg.get("cosmos.service.grag.auto_extract", True)
    context_length = cfg.get("cosmos.service.grag.context_length", 10)
    similarity_threshold = cfg.get("cosmos.service.grag.similarity_threshold", 0.7)
    session_tracking = cfg.get("cosmos.service.grag.session_tracking", True)

    # 加载 Neo4j 配置
    neo4j_cfg = cfg.get("cosmos.service.grag.neo4j") or {}
    neo4j = Neo4jConfig(
        uri=neo4j_cfg.get("uri", "bolt://localhost:7687"),
        user=neo4j_cfg.get("user", "neo4j"),
        password=neo4j_cfg.get("password"),  # 无默认值，运行时检查
        database=neo4j_cfg.get("database", "neo4j"),
    )

    # 加载提取器配置
    extractor_cfg = cfg.get("cosmos.service.grag.extractor") or {}
    extractor = ExtractorConfig(
        max_retries=extractor_cfg.get("max_retries", 2),
        timeout=extractor_cfg.get("timeout", 30),
    )

    # 加载任务管理器配置
    task_cfg = cfg.get("cosmos.service.grag.task_manager") or {}
    task_manager = TaskManagerConfig(
        max_workers=task_cfg.get("max_workers", 3),
        max_queue_size=task_cfg.get("max_queue_size", 100),
        task_timeout=task_cfg.get("task_timeout", 30),
        auto_cleanup_hours=task_cfg.get("auto_cleanup_hours", 24),
    )

    return GRAGConfig(
        enabled=grag_enabled,
        auto_extract=auto_extract,
        context_length=context_length,
        similarity_threshold=similarity_threshold,
        session_tracking=session_tracking,
        neo4j=neo4j,
        extractor=extractor,
        task_manager=task_manager,
    )


def reload_config() -> GRAGConfig:
    """重新加载配置，清除缓存"""
    global _config
    with _config_lock:
        _config = None
    return get_grag_config()


__all__ = [
    "GRAGConfig",
    "Neo4jConfig",
    "ExtractorConfig",
    "TaskManagerConfig",
    "get_grag_config",
    "reload_config",
    "init_config_listener",
]
