"""知识库懒加载单例。"""

from __future__ import annotations

import threading
from pathlib import Path

from core.logger import get_logger
from core.vector.config import VectorConfig

from agent.knowledge.loader import load_directory
from agent.knowledge.store import KnowledgeStore

logger = get_logger(__name__)

_store: KnowledgeStore | None = None
_store_lock = threading.Lock()


def get_knowledge_store(config: VectorConfig | None = None) -> KnowledgeStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = KnowledgeStore(config)
    return _store


def reset_knowledge_store() -> None:
    """重置知识库单例（主要用于测试隔离）。"""
    global _store
    with _store_lock:
        _store = None


async def index_knowledge_directory(directory: str | Path) -> int:
    """启动时一次性索引目录下全部 .md，返回索引片段总数；目录不存在/为空时安全跳过。"""
    store = get_knowledge_store()
    docs = load_directory(directory)
    if not docs:
        logger.info("知识库目录为空或不存在，跳过索引: %s", directory)
        return 0
    total = 0
    for doc_id, title, chunks in docs:
        await store.index_document(doc_id, title, chunks)
        total += len(chunks)
    logger.info("知识库索引完成：%d 篇文档，%d 个片段", len(docs), total)
    return total
