"""TTS 提供商抽象基类与工厂"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, AsyncIterator

from core.tts.models import TTSRequest


class TTSProvider(ABC):
    """
    TTS 提供商抽象基类。

    会话式流式合成模式：create_session → consume_session → close_session。

    支持异步上下文管理器协议，自动管理资源生命周期：

    .. code-block:: python

        async with TTSProviderFactory.create("astra", config) as provider:
            session_id = await provider.create_session(request)
            async for chunk in provider.consume_session(session_id):
                # 处理音频块
                pass
            await provider.close_session(session_id)
        # provider.aclose() 自动调用

    Args:
        config: 提供商配置字典。
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        raw = config.get("timeout", 60)
        self.timeout: int | None = raw if raw else None  # 0 或 None 表示无超时

    @abstractmethod
    async def create_session(self, request: TTSRequest) -> str:
        """创建合成会话，返回 session_id。"""

    @abstractmethod
    def consume_session(self, session_id: str) -> AsyncIterator[bytes]:
        """消费会话音频流，逐块 yield 音频数据。"""

    @abstractmethod
    async def close_session(self, session_id: str) -> None:
        """释放会话资源。"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称标识符。"""

    async def aclose(self) -> None:
        """释放提供商持有的资源，默认空实现，子类按需覆盖。"""

    async def __aenter__(self) -> "TTSProvider":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class TTSProviderFactory:
    """
    TTS 提供商工厂，维护名称到提供商类的注册表，按需创建实例。

    内置提供商在 ``core/tts/__init__.py`` 中统一注册，
    外部扩展可通过 ``register()`` 注入自定义提供商。

    Examples:
        >>> TTSProviderFactory.register("my_tts", MyTTSProvider)
        >>> provider = TTSProviderFactory.create("my_tts", config)
    """

    _registry: dict[str, type[TTSProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[TTSProvider]) -> None:
        """注册提供商类型，已存在的名称将被覆盖。"""
        cls._registry[name] = provider_cls

    @classmethod
    def create(cls, name: str, config: dict[str, Any]) -> TTSProvider:
        """
        根据名称创建提供商实例。

        Args:
            name: 已注册的提供商名称。
            config: 传递给提供商构造函数的配置字典。

        Returns:
            具体提供商实例。

        Raises:
            TTSProviderNotFoundError: 名称未注册时抛出。
        """
        from core.tts.exceptions import TTSProviderNotFoundError

        if name not in cls._registry:
            raise TTSProviderNotFoundError(name)
        return cls._registry[name](config)

    @classmethod
    def detect_from_config(cls, tts_section: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        从外部配置文件中加载提供商配置。

        配置格式：`{"providers": {"name": "astra", "config_path": "data/config/TTSProviders.json"}}`

        Args:
            tts_section: TTS 配置节点字典。

        Returns:
            ``(provider_name, provider_config)`` 元组。

        Raises:
            TTSProviderNotFoundError: 配置中未找到提供商名称、配置路径或外部文件加载失败时抛出。

        Examples:
            >>> section = {"providers": {"name": "astra", "config_path": "data/config/TTSProviders.json"}}
            >>> name, config = TTSProviderFactory.detect_from_config(section)
        """
        import json
        from pathlib import Path
        from core.tts.exceptions import TTSProviderNotFoundError
        
        providers = tts_section.get("providers", {})
        
        if not isinstance(providers, dict):
            raise TTSProviderNotFoundError(
                "配置格式错误：providers 必须是字典类型"
            )
        
        provider_name = providers.get("name")
        if not provider_name:
            raise TTSProviderNotFoundError(
                "配置中未找到 providers.name 字段，请使用格式：\n"
                '{"providers": {"name": "astra", "config_path": "data/config/TTSProviders.json"}}'
            )
        
        config_path = providers.get("config_path")
        if not config_path:
            raise TTSProviderNotFoundError(
                "配置中未找到 providers.config_path 字段，必须指定外部配置文件路径，格式：\n"
                '{"providers": {"name": "astra", "config_path": "data/config/TTSProviders.json"}}'
            )
        
        try:
            path = Path(config_path)
            with path.open("r", encoding="utf-8") as f:
                external_configs = json.load(f)
            
            if not isinstance(external_configs, dict):
                raise TTSProviderNotFoundError(
                    f"外部配置文件格式错误：{config_path}，期望 JSON 对象"
                )
            
            if provider_name not in external_configs:
                available = list(external_configs.keys())
                raise TTSProviderNotFoundError(
                    f"外部配置文件 {config_path} 中未找到提供商 '{provider_name}'。\n"
                    f"可用的提供商：{available}"
                )
            
            from core.config.env_resolver import resolve_env_vars as _resolve_env_vars
            
            provider_config = external_configs[provider_name]
            return provider_name, _resolve_env_vars(provider_config)
        
        except FileNotFoundError as e:
            raise TTSProviderNotFoundError(
                f"外部配置文件不存在：{config_path}"
            ) from e
        except json.JSONDecodeError as e:
            raise TTSProviderNotFoundError(
                f"外部配置文件 JSON 解析失败：{config_path}\n{e}"
            ) from e
