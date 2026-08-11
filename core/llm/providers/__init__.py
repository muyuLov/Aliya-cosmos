"""LLM 提供商模块：统一 OpenAI 兼容接口"""

from core.llm.providers.base import LLMProvider
from core.llm.providers.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
]
