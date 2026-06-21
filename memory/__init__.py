"""memory - GRAG 知识图谱记忆系统

借鉴 NagaAgent-main/summer_memory 架构，采用项目 core/llm/ 替换直接 API 调用。

模块结构:
    _providers.py   - 共享 LLM Provider 懒加载工具
    config.py       - GRAG 配置加载
    extractor.py    - 五元组提取（使用 core/llm/）
    graph.py        - Neo4j 图谱操作（Schema v3: Entity标签+属性类型，去APOC依赖）
    rag_query.py    - RAG 知识查询（使用 core/llm/）
    task_manager.py - 并发任务管理（懒加载工厂）
    memory_manager.py - 记忆管理器（集成层）

兼容接口：
    create_memory_service(config_path) -> (builder, engine, recall, client, extractor)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Tuple

from core.logger import get_logger
from memory.config import get_grag_config, reload_config
from memory.exceptions import (
    GRAGError,
    GRAGConfigError,
    GRAGNotEnabledError,
    GraphOperationError,
    GraphConnectionError,
    GraphQueryError,
    GraphWriteError,
    ExtractionError,
    ExtractionTimeoutError,
    ExtractionParseError,
    LLMProviderError,
    RAGQueryError,
    RAGContextError,
    RAGGenerationError,
    TaskManagerError,
    TaskQueueFullError,
    TaskTimeoutError,
    TaskExecutionError,
)
from memory.extractor import QuintupleExtractor, get_extractor
from memory.graph import (
    GraphStore,
    clear_all_quintuples,
    clear_all_quintuples_async,
    get_all_quintuples,
    get_all_quintuples_async,
    get_graph_stats,
    get_graph_stats_async,
    get_graph_store,
    query_graph_by_keywords,
    query_graph_by_keywords_async,
    reset_graph_store,
    set_graph_store,
    store_quintuples,
    store_quintuples_async,
)
from memory.memory_manager import GRAGMemoryManager, get_memory_manager
from memory.rag_query import RAGQueryEngine, get_rag_engine
from memory.task_manager import (
    QuintupleTaskManager,
    get_task_manager,
    start_task_manager,
    stop_task_manager,
)

logger = get_logger(__name__)

# 类型别名
QuintupleType = Tuple[str, str, str, str, str]


def create_memory_service(
    config_path: str | Path = "data/config/main.yml",
) -> Tuple[Any, Any, Any, Any, Any]:
    """
    创建记忆服务（兼容 agent.py 接口）

    Args:
        config_path: 记忆配置文件路径（暂未使用，配置从主配置读取）

    Returns:
        (builder, engine, recall_service, client, extractor)
    """
    # 重新加载配置
    reload_config()

    cfg = get_grag_config()
    if not cfg.enabled:
        raise RuntimeError("GRAG 记忆系统未启用，请检查配置")

    # 1. builder -> memory_manager（负责添加对话记忆）
    builder = get_memory_manager()

    # 2. engine -> graph 模块（图谱操作引擎，含同步/异步接口）
    engine = {
        "store": store_quintuples,
        "store_async": store_quintuples_async,
        "query": query_graph_by_keywords,
        "query_async": query_graph_by_keywords_async,
        "get_all": get_all_quintuples,
        "get_all_async": get_all_quintuples_async,
        "clear": clear_all_quintuples,
        "clear_async": clear_all_quintuples_async,
        "stats": get_graph_stats,
        "stats_async": get_graph_stats_async,
    }

    # 3. recall_service -> rag_query 引擎（RAG 召回）
    recall = get_rag_engine()

    # 4. client -> task_manager（任务管理器客户端）
    client = get_task_manager()

    # 5. extractor -> 五元组提取器
    ext = get_extractor()

    # 启动任务管理器
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(start_task_manager())
    except RuntimeError:
        # 没有运行中的事件循环，延迟启动
        pass

    logger.info("记忆服务创建成功")
    return builder, engine, recall, client, ext


def get_service_status() -> dict[str, Any]:
    """获取记忆服务状态"""
    return get_memory_manager().get_memory_stats()


__all__ = [
    # 核心服务
    "create_memory_service",
    "get_memory_manager",
    "get_service_status",
    # 配置
    "get_grag_config",
    "reload_config",
    # 组件
    "get_extractor",
    "get_rag_engine",
    "get_task_manager",
    # 图谱操作（同步）
    "store_quintuples",
    "query_graph_by_keywords",
    "get_all_quintuples",
    "clear_all_quintuples",
    "get_graph_stats",
    # 图谱操作（异步）
    "store_quintuples_async",
    "query_graph_by_keywords_async",
    "get_all_quintuples_async",
    "clear_all_quintuples_async",
    "get_graph_stats_async",
    # 图谱存储管理（测试隔离）
    "GraphStore",
    "get_graph_store",
    "set_graph_store",
    "reset_graph_store",
    # 任务管理
    "start_task_manager",
    "stop_task_manager",
    # 异常
    "GRAGError",
    "GRAGConfigError",
    "GRAGNotEnabledError",
    "GraphOperationError",
    "GraphConnectionError",
    "GraphQueryError",
    "GraphWriteError",
    "ExtractionError",
    "ExtractionTimeoutError",
    "ExtractionParseError",
    "LLMProviderError",
    "RAGQueryError",
    "RAGContextError",
    "RAGGenerationError",
    "TaskManagerError",
    "TaskQueueFullError",
    "TaskTimeoutError",
    "TaskExecutionError",
]
