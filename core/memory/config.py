"""GRAG 配置加载模块"""

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from core.config import get_config_instance
from core.config.env_resolver import mask_sensitive
from core.logger import get_logger

logger = get_logger(__name__)


def _check_type(
    value: Any,
    key: str,
    expected_type: type | tuple[type, ...],
    min_val: int | float | None = None,
    max_val: int | float | None = None,
) -> None:
    """校验配置值的类型和范围，不符合时抛出 GRAGConfigError

    Args:
        value:         配置值
        key:           配置路径（用于错误消息）
        expected_type: 期望的类型（支持 type 或 type 元组，如 (int, float)）
        min_val:       最小值（可选，含边界）
        max_val:       最大值（可选，含边界）
    """
    from core.memory.exceptions import GRAGConfigError

    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            type_desc = '|'.join(t.__name__ for t in expected_type)
        else:
            type_desc = expected_type.__name__
        raise GRAGConfigError(
            f"{key}: 期望类型 {type_desc}，实际 {type(value).__name__} = {value!r}"
        )
    if min_val is not None and value < min_val:
        raise GRAGConfigError(
            f"{key}: 值 {value} 小于最小值 {min_val}"
        )
    if max_val is not None and value > max_val:
        raise GRAGConfigError(
            f"{key}: 值 {value} 大于最大值 {max_val}"
        )


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
    cleanup_interval_seconds: int = 3600


@dataclass
class GRAGConfig:
    """GRAG 知识图谱记忆系统配置"""
    enabled: bool = True
    auto_extract: bool = True
    context_length: int = 10
    similarity_threshold: float = 0.7
    session_tracking: bool = True
    ai_name: str = "Aliya"
    user_name: str = "cosmos"
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
    """从配置文件加载 GRAG 配置（含类型和范围校验）"""
    cfg = get_config_instance(config_path)

    # 加载顶层配置
    grag_enabled = cfg.get("cosmos.service.grag.enabled", True)
    auto_extract = cfg.get("cosmos.service.grag.auto_extract", True)
    context_length = cfg.get("cosmos.service.grag.context_length", 10)
    similarity_threshold = cfg.get("cosmos.service.grag.similarity_threshold", 0.7)
    session_tracking = cfg.get("cosmos.service.grag.session_tracking", True)
    ai_name = cfg.get("cosmos.characters.ai_name", "Aliya")
    user_name = cfg.get("cosmos.characters.user_name", "cosmos")

    # 类型和范围校验
    _check_type(grag_enabled, "cosmos.service.grag.enabled", bool)
    _check_type(auto_extract, "cosmos.service.grag.auto_extract", bool)
    _check_type(context_length, "cosmos.service.grag.context_length", int, min_val=1)
    _check_type(similarity_threshold, "cosmos.service.grag.similarity_threshold", (int, float), min_val=0.0, max_val=1.0)
    _check_type(session_tracking, "cosmos.service.grag.session_tracking", bool)
    _check_type(ai_name, "cosmos.characters.ai_name", str)
    _check_type(user_name, "cosmos.characters.user_name", str)

    # 加载 Neo4j 配置
    neo4j_cfg = cfg.get("cosmos.service.grag.neo4j") or {}
    neo4j_uri = neo4j_cfg.get("uri", "bolt://localhost:7687")
    neo4j_user = neo4j_cfg.get("user", "neo4j")
    neo4j_password = neo4j_cfg.get("password")  # 无默认值，运行时检查
    neo4j_database = neo4j_cfg.get("database", "neo4j")

    _check_type(neo4j_uri, "cosmos.service.grag.neo4j.uri", str)
    _check_type(neo4j_user, "cosmos.service.grag.neo4j.user", str)
    _check_type(neo4j_database, "cosmos.service.grag.neo4j.database", str)
    if neo4j_password is not None:
        _check_type(neo4j_password, "cosmos.service.grag.neo4j.password", str)

    # fail-fast: enabled=True 时 password 必须非空
    if grag_enabled and (not neo4j_password or not neo4j_password.strip()):
        from core.memory.exceptions import GRAGConfigError
        raise GRAGConfigError(
            "cosmos.service.grag.neo4j.password: 启用 GRAG 时必须配置 Neo4j 密码"
        )

    neo4j = Neo4jConfig(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        database=neo4j_database,
    )

    _log_masked_cfg = mask_sensitive({
        "uri": neo4j_uri,
        "user": neo4j_user,
        "password": neo4j_password,
    })
    logger.debug("Neo4j 连接配置: %s", _log_masked_cfg)

    # 加载提取器配置
    extractor_cfg = cfg.get("cosmos.service.grag.extractor") or {}
    extractor_max_retries = extractor_cfg.get("max_retries", 2)
    extractor_timeout = extractor_cfg.get("timeout", 30)

    _check_type(extractor_max_retries, "cosmos.service.grag.extractor.max_retries", int, min_val=0)
    _check_type(extractor_timeout, "cosmos.service.grag.extractor.timeout", int, min_val=1)

    extractor = ExtractorConfig(
        max_retries=extractor_max_retries,
        timeout=extractor_timeout,
    )

    # 加载任务管理器配置
    task_cfg = cfg.get("cosmos.service.grag.task_manager") or {}
    task_max_workers = task_cfg.get("max_workers", 3)
    task_max_queue_size = task_cfg.get("max_queue_size", 100)
    task_timeout = task_cfg.get("task_timeout", 30)
    task_auto_cleanup = task_cfg.get("auto_cleanup_hours", 24)
    task_cleanup_interval = task_cfg.get("cleanup_interval_seconds", 3600)

    _check_type(task_max_workers, "cosmos.service.grag.task_manager.max_workers", int, min_val=1)
    _check_type(task_max_queue_size, "cosmos.service.grag.task_manager.max_queue_size", int, min_val=1)
    _check_type(task_timeout, "cosmos.service.grag.task_manager.task_timeout", int, min_val=1)
    _check_type(task_auto_cleanup, "cosmos.service.grag.task_manager.auto_cleanup_hours", int, min_val=1)
    _check_type(task_cleanup_interval, "cosmos.service.grag.task_manager.cleanup_interval_seconds", int, min_val=60)

    task_manager = TaskManagerConfig(
        max_workers=task_max_workers,
        max_queue_size=task_max_queue_size,
        task_timeout=task_timeout,
        auto_cleanup_hours=task_auto_cleanup,
        cleanup_interval_seconds=task_cleanup_interval,
    )

    logger.debug("GRAG 配置加载并校验完成")
    return GRAGConfig(
        enabled=grag_enabled,
        auto_extract=auto_extract,
        context_length=context_length,
        similarity_threshold=similarity_threshold,
        session_tracking=session_tracking,
        ai_name=ai_name,
        user_name=user_name,
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
