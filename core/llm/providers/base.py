"""LLM 提供商抽象基类与工厂"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import openai

from core.llm.exceptions import ProviderNotFoundError
from core.llm.models import ChatRequest, ChatResponse, TokenUsage

# ── 公共 OpenAI 兼容异常元组 ───────────────────────────────────────────────────
# 供所有基于 openai SDK 的提供商（DeepSeek、LMStudio 等）统一引用，
# 避免各提供商各自维护重复的异常列表。

#: 纯网络/API 层异常（连接失败、超时、限流、HTTP 状态码错误）
OPENAI_NETWORK_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.APIStatusError,
)

#: 完整异常元组：数据类型错误 + 网络层异常，适用于大多数调用点
OPENAI_COMMON_EXCEPTIONS: tuple[type[Exception], ...] = (
    ValueError,
    TypeError,
    AttributeError,
    *OPENAI_NETWORK_EXCEPTIONS,
)


def _safe_int(value: Any) -> int:
    """安全提取 int，None/非数字返回 0。"""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get_nested(obj: Any, *attrs: str, default: Any = None) -> Any:
    """安全地链式访问嵌套属性。"""
    current = obj
    for attr in attrs:
        if current is None:
            return default
        current = getattr(current, attr, None)
    return current


def extract_openai_usage(usage: Any) -> TokenUsage:
    """
    从 OpenAI SDK 的 usage 对象中提取 TokenUsage，字段缺失时默认 0。

    适配多个 API 版本：
    - **顶级字段**：prompt_tokens, completion_tokens, total_tokens（所有提供商均提供）
    - **completion_tokens_details**（OpenAI/DeepSeek）:
      - reasoning_tokens：思维链 token 数
    - **prompt_tokens_details**（部分提供商）:
      - cached_tokens：缓存命中 token 数
    - **顶级扩展字段**（DeepSeek 专有）:
      - prompt_cache_hit_tokens：缓存命中 token 数
      - prompt_cache_miss_tokens：缓存未命中 token 数

    字段缺失或为 None 时自动回退为 0，返回值与官方计费标准对齐。
    """
    if usage is None:
        return TokenUsage()

    # 基础 token 字段（所有 OpenAI 兼容接口均提供）
    prompt_tokens = _safe_int(getattr(usage, "prompt_tokens", None))
    completion_tokens = _safe_int(getattr(usage, "completion_tokens", None))
    api_total = _safe_int(getattr(usage, "total_tokens", None))

    # total_tokens 以 API 返回值优先，缺失时由 prompt + completion 补齐
    total_tokens = api_total if api_total > 0 else prompt_tokens + completion_tokens

    # 缓存字段：优先读取 DeepSeek 顶层字段，再 fallback 到 prompt_tokens_details
    cache_hit = _safe_int(getattr(usage, "prompt_cache_hit_tokens", None))
    if cache_hit == 0:
        cache_hit = _safe_int(_get_nested(usage, "prompt_tokens_details", "cached_tokens"))
    cache_miss = _safe_int(getattr(usage, "prompt_cache_miss_tokens", None))

    # 思维链 token：从 completion_tokens_details 读取
    details = _get_nested(usage, "completion_tokens_details")
    reasoning = _safe_int(getattr(details, "reasoning_tokens", None) if details is not None else None)

    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
        reasoning_tokens=reasoning,
    )


class LLMProvider(ABC):
    """
    LLM 提供商抽象基类，所有具体提供商必须继承此类并实现抽象方法。

    从配置字典中提取公共字段（model、timeout、max_retries），
    子类可通过 self.config 访问完整配置。

    支持异步上下文管理器协议，自动管理资源生命周期：

    .. code-block:: python

        async with ProviderFactory.create("deepseek", config) as provider:
            response = await provider.async_chat_completion(request)
        # provider.aclose() 自动调用

    Attributes:
        supports_reasoning: 当前模型是否支持思维链/推理 token 特性。
                            子类可覆盖此属性以实现自定义检测逻辑。
        last_stream_usage: 最近一次流式调用的 token 用量，供上层累计统计。
                            非流式场景请使用 async_chat_completion 返回值中的 usage。

    Args:
        config: 提供商配置字典，至少包含 model 字段。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.model: str = config.get("model", "")
        self.timeout: int = config.get("timeout", 600)
        self.max_retries: int = config.get("max_retries", 3)
        self.last_stream_usage: TokenUsage = TokenUsage()

    @property
    def supports_reasoning(self) -> bool:
        """当前模型是否支持思维链/推理特性。

        默认实现基于模型名称检测（包含 deepseek/r1 关键词），
        子类可覆盖此属性以实现更精确的检测逻辑。
        """
        model_lower = self.model.lower()
        return "deepseek" in model_lower or "r1" in model_lower

    @abstractmethod
    async def async_chat_completion(self, request: ChatRequest) -> ChatResponse:
        """
        异步对话补全，直接调用底层异步客户端，不阻塞事件循环。

        所有子类必须实现此方法。同步场景可用 ``asyncio.run()`` 驱动。

        Args:
            request: 对话请求。

        Returns:
            对话响应。

        Raises:
            LLMRequestError: 请求失败时抛出。
        """

    async def stream_chat_completion(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """
        流式对话补全，逐 token yield 文本片段。
        默认退化为调用 async_chat_completion 后一次性 yield 全部内容。
        子类应重写此方法以实现真正的流式行为。
        """
        response = await self.async_chat_completion(request)
        yield response.content

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称标识符，用于日志与异常信息中的来源标注。"""

    @staticmethod
    def _or_not_given(value: Any) -> Any:
        """None 时返回 NOT_GIVEN，避免将 None 显式传给 OpenAI SDK。"""
        from openai import NOT_GIVEN
        return value if value is not None else NOT_GIVEN

    async def aclose(self) -> None:
        """释放提供商持有的资源，默认空实现，子类按需覆盖。"""

    async def __aenter__(self) -> "LLMProvider":
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.aclose()


class ProviderFactory:
    """
    LLM 提供商工厂，维护名称到提供商类的注册表，按需创建实例。

    内置提供商在 ``core/llm/__init__.py`` 中统一注册，
    外部扩展可通过 ``register()`` 注入自定义提供商。

    Examples:
        >>> ProviderFactory.register("my_provider", MyProvider)
        >>> provider = ProviderFactory.create("my_provider", config)
    """

    _registry: dict[str, type[LLMProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[LLMProvider]) -> None:
        """注册提供商类型，已存在的名称将被覆盖。"""
        cls._registry[name] = provider_cls

    @classmethod
    def create(cls, name: str, config: dict[str, Any]) -> LLMProvider:
        """
        根据名称创建提供商实例。

        Args:
            name: 已注册的提供商名称。
            config: 传递给提供商构造函数的配置字典。

        Returns:
            具体提供商实例。

        Raises:
            ProviderNotFoundError: 名称未注册时抛出。
        """
        if name not in cls._registry:
            raise ProviderNotFoundError(name)
        return cls._registry[name](config)

    @classmethod
    def detect_from_config(cls, llm_section: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        从外部配置文件中加载提供商配置。

        配置格式：`{"providers": {"name": "deepseek", "config_path": "data/config/LLMProviders.json"}}`

        Args:
            llm_section: LLM 配置节点字典。

        Returns:
            ``(provider_name, provider_config)`` 元组。

        Raises:
            ProviderNotFoundError: 配置中未找到提供商名称、配置路径或外部文件加载失败时抛出。

        Examples:
            >>> section = {"providers": {"name": "deepseek", "config_path": "data/config/LLMProviders.json"}}
            >>> name, config = ProviderFactory.detect_from_config(section)
        """
        import json
        from pathlib import Path
        
        providers = llm_section.get("providers", {})
        
        if not isinstance(providers, dict):
            raise ProviderNotFoundError(
                "配置格式错误：providers 必须是字典类型"
            )
        
        provider_name = providers.get("name")
        if not provider_name:
            raise ProviderNotFoundError(
                "配置中未找到 providers.name 字段，请使用格式：\n"
                '{"providers": {"name": "deepseek", "config_path": "data/config/LLMProviders.json"}}'
            )
        
        config_path = providers.get("config_path")
        if not config_path:
            raise ProviderNotFoundError(
                "配置中未找到 providers.config_path 字段，必须指定外部配置文件路径，格式：\n"
                '{"providers": {"name": "deepseek", "config_path": "data/config/LLMProviders.json"}}'
            )
        
        try:
            path = Path(config_path)
            with path.open("r", encoding="utf-8") as f:
                external_configs = json.load(f)
            
            if not isinstance(external_configs, dict):
                raise ProviderNotFoundError(
                    f"外部配置文件格式错误：{config_path}，期望 JSON 对象"
                )
            
            if provider_name not in external_configs:
                available = list(external_configs.keys())
                raise ProviderNotFoundError(
                    f"外部配置文件 {config_path} 中未找到提供商 '{provider_name}'。\n"
                    f"可用的提供商：{available}"
                )
            
            from core.config.env_resolver import resolve_env_vars as _resolve_env_vars
            
            provider_config = external_configs[provider_name]
            return provider_name, _resolve_env_vars(provider_config)
        
        except FileNotFoundError as e:
            raise ProviderNotFoundError(
                f"外部配置文件不存在：{config_path}"
            ) from e
        except json.JSONDecodeError as e:
            raise ProviderNotFoundError(
                f"外部配置文件 JSON 解析失败：{config_path}\n{e}"
            ) from e
