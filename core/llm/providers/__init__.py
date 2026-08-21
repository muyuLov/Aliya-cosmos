"""LLM 提供商模块：统一 OpenAI 兼容接口"""

from core.llm.providers.base import LLMProvider
from core.llm.providers.openai_compatible import OpenAICompatibleProvider
from core.llm.providers.registry import ProviderRegistry

# 默认注册 OpenAI 兼容提供商
ProviderRegistry.register("openai_compatible", OpenAICompatibleProvider)

__all__ = [
    "LLMProvider",
    "ProviderRegistry",
]
