"""记忆系统内部 LLM Provider 工具模块

提供共享的 LLM Provider 懒加载单例，供 extractor.py 和 rag_query.py 复用，
避免两个模块各自重复维护相同的初始化逻辑。
"""

from __future__ import annotations

import threading
from typing import Optional

from core.llm.providers.base import LLMProvider
from core.logger import get_logger

logger = get_logger(__name__)

# 共享 provider 单例（懒加载）
_shared_provider: Optional[LLMProvider] = None
_provider_lock = threading.Lock()


def get_memory_provider() -> LLMProvider:
    """
    获取记忆系统共享 LLM Provider（线程安全懒加载单例）

    自动从主配置读取 cosmos.service.llm 配置，
    使用 OpenAICompatibleProvider 创建实例。

    Returns:
        LLMProvider 实例
    """
    global _shared_provider
    if _shared_provider is None:
        with _provider_lock:
            if _shared_provider is None:
                from core.config import get_config_instance
                from core.llm import _resolve_provider_config
                from core.llm.providers import OpenAICompatibleProvider

                cfg_mgr = get_config_instance("data/config/main.yml")
                llm_section = cfg_mgr.get("cosmos.service.llm") or {}

                provider_config = _resolve_provider_config(llm_section)
                _shared_provider = OpenAICompatibleProvider(provider_config)

                logger.info(
                    "记忆系统 LLM Provider 初始化完成: %s",
                    provider_config.get("provider_name", "openai_compatible"),
                )

    return _shared_provider


def reset_memory_provider() -> None:
    """
    重置共享 Provider（主要用于测试或配置热重载）
    """
    global _shared_provider
    with _provider_lock:
        _shared_provider = None
        logger.debug("记忆系统 LLM Provider 已重置")
