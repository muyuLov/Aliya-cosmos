"""LLM 模块公共接口"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.logger import get_logger as _get_logger
from core.llm.cache import ContextCache
from core.llm.cache_backend import CacheBackend, MemoryBackend
from core.llm.config_validator import ConfigValidator
from core.llm.context_manager import ConversationContextManager
from core.llm.exceptions import (
    ContextCacheError,
    LLMError,
    LLMRequestError,
    ProviderNotFoundError,
)
from core.llm.models import ChatRequest, ChatResponse, ConversationContext, Message
from core.llm.providers import OpenAICompatibleProvider
from core.llm.providers.base import LLMProvider
from core.llm.service import ConversationService

logger = _get_logger(__name__)


def _resolve_provider_config(llm_section: dict[str, Any]) -> dict[str, Any]:
    """
    从外部配置文件加载提供商配置。

    配置格式：
    ``{"providers": {"name": "deepseek", "config_path": "data/config/LLMProviders.json"}}``

    返回包含 ``url``、``model``、``api_key`` 等字段以及与原始名称对应的 ``provider_name``。

    Raises:
        ProviderNotFoundError: 配置不完整或外部文件加载失败时抛出。
    """
    from core.config.provider_loader import load_provider_config

    provider_name, config = load_provider_config(llm_section, ProviderNotFoundError)
    config["provider_name"] = provider_name
    return config


def create_service(
    provider_config: dict[str, Any],
    *,
    history_max_chars: int = 90000,
    conversation_id: str | None = None,
    cache: ContextCache | None = None,
    system_prompt: str | None = None,
    system_prompt_file: str | Path | None = None,
) -> ConversationService:
    """
    使用提供商配置创建对话服务实例。

    Args:
        provider_config: OpenAI 兼容提供商配置，至少包含 ``url``、``model`` 字段，
                         可选 ``api_key``、``timeout``、``max_retries``、``http2``。
        history_max_chars: 消息历史总字符数阈值，默认 90000。
        conversation_id: 会话 ID，为 None 时自动生成。
        cache: 上下文缓存实例，为 None 时创建新的内存缓存。
        system_prompt: 系统提示词文本。
        system_prompt_file: 从文件加载系统提示词（优先级高于 system_prompt）。

    Returns:
        配置好的 ConversationService 实例。
    """
    provider = OpenAICompatibleProvider(provider_config)
    _cache = cache or ContextCache()

    if system_prompt_file is not None:
        try:
            system_prompt = Path(system_prompt_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ProviderNotFoundError(
                f"系统提示词文件不存在: {system_prompt_file}"
            )

    return ConversationService(
        provider=provider,
        cache=_cache,
        history_max_chars=history_max_chars,
        conversation_id=conversation_id,
        system_prompt=system_prompt,
    )


def create_from_config(
    config_path: str | Path = "data/config/main.yml",
    config_prefix: str = "cosmos.service.llm",
    *,
    conversation_id: str | None = None,
    cache: ContextCache | None = None,
    system_prompt: str | None = None,
    system_prompt_file: str | Path | None = None,
) -> ConversationService:
    """
    从主配置文件加载提供商配置并创建对话服务实例。

    配置格式：

    .. code-block:: yaml

        llm:
          providers:
            name: lmstudio
            config_path: data/config/LLMProviders.json
          history_max_chars: 90000
          system_prompt_file: agent/prompts/aliya_system_prompt.md

    LLMProviders.json 按标准 OpenAI 兼容格式组织，每个条目包含 url、model、api_key 等字段，
    无需区分提供商类型：

    .. code-block:: json

        {
          "deepseek": {
            "url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key": "${DEEPSEEK_API_KEY}"
          },
          "ollama": {
            "url": "http://localhost:11434",
            "model": "qwen2.5:14b"
          },
          "lmstudio": {
            "url": "http://localhost:1234",
            "model": "qwen2.5-7b-instruct",
            "http2": false
          }
        }

    Args:
        config_path: 主配置文件路径，默认 ``data/config/main.yml``。
        config_prefix: 配置节点路径前缀，默认 ``cosmos.service.llm``。
        conversation_id: 会话 ID，为 None 时自动生成。
        cache: 上下文缓存实例，为 None 时创建新的内存缓存。
        system_prompt: 系统提示词文本（优先级低于文件）。
        system_prompt_file: 从文件加载系统提示词（优先级高于 system_prompt）。

    Returns:
        配置好的 ConversationService 实例。

    Raises:
        ProviderNotFoundError: 配置节点不存在或解析提供商配置失败时抛出。
    """
    from core.config import get_config_instance

    cfg = get_config_instance(str(config_path))
    llm_section: dict[str, Any] = cfg.get(config_prefix) or {}

    if not llm_section:
        raise ProviderNotFoundError(f"配置节点 {config_prefix} 不存在或为空")

    # 验证配置（非严格模式，只记录警告）
    if not ConfigValidator.validate_and_log(llm_section, strict=False):
        logger.warning("LLM 配置验证存在异常，服务将使用当前配置继续创建")

    # 从配置文件解析提供商配置
    provider_config = _resolve_provider_config(llm_section)

    # 从配置中读取覆盖参数，调用方显式传入的优先级更高
    history_max_chars: int = llm_section.get("history_max_chars", 90000)
    cfg_system_prompt: str | None = llm_section.get("system_prompt")
    cfg_system_prompt_file: str | None = llm_section.get("system_prompt_file")

    return create_service(
        provider_config,
        history_max_chars=history_max_chars,
        conversation_id=conversation_id,
        cache=cache,
        system_prompt=system_prompt if system_prompt is not None else cfg_system_prompt,
        system_prompt_file=system_prompt_file if system_prompt_file is not None else cfg_system_prompt_file,
    )


__all__ = [
    # 核心服务
    "ConversationService",
    "ConversationContextManager",
    "create_service",
    "create_from_config",
    # 提供商
    "LLMProvider",
    "OpenAICompatibleProvider",
    # 缓存
    "ContextCache",
    "CacheBackend",
    "MemoryBackend",
    # 数据模型
    "Message",
    "ConversationContext",
    "ChatRequest",
    "ChatResponse",
    # 异常
    "LLMError",
    "LLMRequestError",
    "ProviderNotFoundError",
    "ContextCacheError",
    # 配置验证
    "ConfigValidator",
]
