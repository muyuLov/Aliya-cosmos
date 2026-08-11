"""Vector 向量模块配置加载"""

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from core.config import get_config_instance
from core.logger import get_logger
from core.vector.exceptions import VectorConfigError

logger = get_logger(__name__)


def _check_type(
    value: Any,
    key: str,
    expected_type: type | tuple[type, ...],
    min_val: int | float | None = None,
    max_val: int | float | None = None,
) -> None:
    """校验配置值的类型和范围，不符合时抛出 VectorConfigError

    Args:
        value:         配置值
        key:           配置路径（用于错误消息）
        expected_type: 期望的类型（支持 type 或 type 元组，如 (int, float)）
        min_val:       最小值（可选，含边界）
        max_val:       最大值（可选，含边界）
    """
    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            type_desc = "|".join(t.__name__ for t in expected_type)
        else:
            type_desc = expected_type.__name__
        raise VectorConfigError(
            f"{key}: 期望类型 {type_desc}，实际 {type(value).__name__} = {value!r}"
        )
    if min_val is not None and value < min_val:
        raise VectorConfigError(
            f"{key}: 值 {value} 小于最小值 {min_val}"
        )
    if max_val is not None and value > max_val:
        raise VectorConfigError(
            f"{key}: 值 {value} 大于最大值 {max_val}"
        )


@dataclass
class EmbeddingConfig:
    """Embedding 生成器配置（固定使用 OpenAI 兼容 Embedding API）

    Attributes:
        model:       embedding 模型名（如 text-embedding-3-small），必须显式配置
        url:         API 服务地址，必须显式配置
        api_key:     API 密钥（本地服务如 Ollama/LM Studio 可留空，使用占位符）
        batch_size:  批量向量化的文本条数上限
        concurrency: 单次向量化并发批次上限（本地服务推理串行，过大只会排队）
        dimension:   期望的向量维度（可选，0=未知由 API 返回自动推断）
    """
    model: str = ""
    url: str = ""
    api_key: str = ""
    batch_size: int = 16
    concurrency: int = 4
    dimension: int = 0


@dataclass
class VectorConfig:
    """向量模块配置

    Attributes:
        enabled:               是否启用向量模块。
        similarity_threshold:  检索相似度阈值（0-1），低于阈值的条目被过滤。
        top_k:                 检索默认返回条数上限。
        embedding:             Embedding 生成器配置。
        storage:               存储后端："memory"（进程内存，重启即清空）/
                               "milvus"（Milvus 向量数据库持久化，连接失败自动回退内存）。
        milvus_uri:            Milvus 服务地址（standalone 默认 http://localhost:19530）。
        milvus_collection:     Milvus 集合名（向量记忆持久化容器）。
    """
    enabled: bool = True
    similarity_threshold: float = 0.5
    top_k: int = 5
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    storage: str = "memory"
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "aliya_memory"


# 全局配置实例 - 懒加载从统一配置管理器读取
_config: Optional[VectorConfig] = None
_config_initialized = False
_config_lock = threading.Lock()


def _on_config_change(path: str, _value: object) -> None:
    """配置变更回调，清除缓存"""
    global _config
    with _config_lock:
        if path.startswith("cosmos.service.vector"):
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
        cfg.register_callback("cosmos.service.vector", _on_config_change)
        _config_initialized = True


def get_vector_config(config_path: str = "data/config/main.yml") -> VectorConfig:
    """
    获取向量模块配置。

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
                _config = _load_vector_config(config_path)
    return _config


def _load_vector_config(config_path: str) -> VectorConfig:
    """从配置文件加载向量配置（含类型和范围校验）"""
    cfg = get_config_instance(config_path)

    # 加载顶层配置
    enabled = cfg.get("cosmos.service.vector.enabled", True)
    similarity_threshold = cfg.get("cosmos.service.vector.similarity_threshold", 0.5)
    top_k = cfg.get("cosmos.service.vector.top_k", 5)
    storage = cfg.get("cosmos.service.vector.storage", "memory")
    milvus_uri = cfg.get(
        "cosmos.service.vector.milvus_uri", "http://localhost:19530"
    )
    milvus_collection = cfg.get(
        "cosmos.service.vector.milvus_collection", "aliya_memory"
    )

    # 类型和范围校验
    _check_type(enabled, "cosmos.service.vector.enabled", bool)
    _check_type(
        similarity_threshold,
        "cosmos.service.vector.similarity_threshold",
        (int, float),
        min_val=0.0,
        max_val=1.0,
    )
    _check_type(top_k, "cosmos.service.vector.top_k", int, min_val=1)
    _check_type(storage, "cosmos.service.vector.storage", str)
    if storage not in ("memory", "milvus"):
        raise VectorConfigError(
            f"cosmos.service.vector.storage: 仅支持 'memory' 或 'milvus'，实际 {storage!r}"
        )
    _check_type(milvus_uri, "cosmos.service.vector.milvus_uri", str)
    _check_type(milvus_collection, "cosmos.service.vector.milvus_collection", str)

    # 加载 embedding 配置
    embedding_cfg = cfg.get("cosmos.service.vector.embedding") or {}
    embedding_model = embedding_cfg.get("model", "")
    embedding_url = embedding_cfg.get("url", "")
    embedding_api_key = embedding_cfg.get("api_key", "")
    batch_size = embedding_cfg.get("batch_size", 16)
    concurrency = embedding_cfg.get("concurrency", 4)
    embedding_dimension = embedding_cfg.get("dimension", 0)

    _check_type(embedding_model, "cosmos.service.vector.embedding.model", str)
    _check_type(embedding_url, "cosmos.service.vector.embedding.url", str)
    _check_type(embedding_api_key, "cosmos.service.vector.embedding.api_key", str)
    _check_type(batch_size, "cosmos.service.vector.embedding.batch_size", int, min_val=1, max_val=128)
    _check_type(concurrency, "cosmos.service.vector.embedding.concurrency", int, min_val=1, max_val=32)
    _check_type(
        embedding_dimension,
        "cosmos.service.vector.embedding.dimension",
        int,
        min_val=0,
        max_val=32768,
    )

    embedding = EmbeddingConfig(
        model=embedding_model,
        url=embedding_url,
        api_key=embedding_api_key,
        batch_size=batch_size,
        concurrency=concurrency,
        dimension=embedding_dimension,
    )

    logger.debug(
        "向量配置加载并校验完成: model=%s, threshold=%.2f",
        embedding_model,
        similarity_threshold,
    )
    return VectorConfig(
        enabled=enabled,
        similarity_threshold=similarity_threshold,
        top_k=top_k,
        embedding=embedding,
        storage=storage,
        milvus_uri=milvus_uri,
        milvus_collection=milvus_collection,
    )


def reload_config() -> VectorConfig:
    """重新加载配置，清除缓存"""
    global _config
    with _config_lock:
        _config = None
    return get_vector_config()


__all__ = [
    "VectorConfig",
    "EmbeddingConfig",
    "get_vector_config",
    "reload_config",
    "init_config_listener",
]
